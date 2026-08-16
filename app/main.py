"""ai-hawk - servidor FastAPI.

Expoe uma API compativel com a da OpenAI (/v1/models, /v1/chat/completions)
por cima de varios provedores de LLM, mais uma interface de chat em /.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.auth import require_api_key
from app.config import get_settings
from app.providers.base import ProviderError
from app.registry import ModelNotFound, PaidModelBlocked, Registry
from app.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    ModelCard,
    ModelList,
    Usage,
    error_payload,
    stream_chunk,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
log = logging.getLogger("ai-hawk")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.registry = Registry(settings)

    log.info("ai-hawk %s iniciando", __version__)
    log.info(
        "Guarda de custo: %s",
        "DESATIVADA - modelos pagos liberados"
        if settings.allow_paid_models
        else "ATIVA - somente modelos gratuitos",
    )
    if not settings.auth_enabled:
        log.warning(
            "HAWK_API_KEYS vazio: autenticacao DESLIGADA. "
            "Defina uma chave no .env antes de expor este servidor."
        )
    if not app.state.registry.providers:
        log.warning(
            "Nenhum provedor ativo. Instale o Ollama (custo zero) ou preencha "
            "uma chave de free tier no .env."
        )
    try:
        yield
    finally:
        await app.state.registry.aclose()
        log.info("ai-hawk encerrado")


app = FastAPI(
    title="ai-hawk",
    version=__version__,
    description="Servidor de IA multi-provedor com API compativel com OpenAI.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def registry(request: Request) -> Registry:
    return request.app.state.registry


# ---------------------------------------------------------------------------
# Tratamento de erros
# ---------------------------------------------------------------------------


@app.exception_handler(ProviderError)
async def _provider_error(_: Request, exc: ProviderError) -> JSONResponse:
    log.warning("Erro do provedor %s: %s", exc.provider, exc.message)
    return JSONResponse(
        status_code=exc.status,
        content=error_payload(exc.message, "provider_error", exc.provider),
    )


@app.exception_handler(ModelNotFound)
async def _model_not_found(_: Request, exc: ModelNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=error_payload(str(exc), "invalid_request_error", "model_not_found"),
    )


@app.exception_handler(PaidModelBlocked)
async def _paid_blocked(_: Request, exc: PaidModelBlocked) -> JSONResponse:
    return JSONResponse(
        status_code=402,
        content=error_payload(str(exc), "payment_required", "paid_model_blocked"),
    )


# ---------------------------------------------------------------------------
# Rotas de servico
# ---------------------------------------------------------------------------


@app.get("/health", tags=["service"])
async def health(request: Request) -> dict[str, object]:
    settings = get_settings()
    reg = registry(request)
    return {
        "status": "ok",
        "version": __version__,
        "providers": sorted(reg.providers),
        "allow_paid_models": settings.allow_paid_models,
        "auth_enabled": settings.auth_enabled,
    }


# ---------------------------------------------------------------------------
# API compativel com OpenAI
# ---------------------------------------------------------------------------


@app.get("/v1/models", response_model=ModelList, tags=["openai"])
async def list_models(
    request: Request,
    refresh: bool = False,
    _: str = Depends(require_api_key),
) -> ModelList:
    """Lista todos os modelos disponiveis, de todos os provedores ativos."""
    specs = await registry(request).catalog(refresh=refresh)
    return ModelList(
        data=[
            ModelCard(
                id=s.id,
                owned_by=s.provider,
                label=s.label,
                provider=s.provider,
                tier=s.tier,
                context_window=s.context_window,
            )
            for s in specs
        ]
    )


@app.post(
    "/v1/chat/completions",
    tags=["openai"],
    response_model=ChatCompletionResponse,
    # Sem isto toda resposta passaria a carregar "tool_calls": null.
    response_model_exclude_none=True,
)
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    _: str = Depends(require_api_key),
):
    """Completa uma conversa no modelo escolhido pelo campo `model`."""
    reg = registry(request)
    spec = await reg.resolve(body.model)
    provider = reg.provider_for(spec)

    log.info(
        "chat: model=%s provider=%s stream=%s msgs=%d",
        spec.id,
        spec.provider,
        body.stream,
        len(body.messages),
    )

    if body.stream:
        return StreamingResponse(
            _sse(provider, body, spec),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = await provider.chat(body, spec)
    return ChatCompletionResponse(
        model=spec.id,
        choices=[
            Choice(
                message=ChoiceMessage(
                    # Com tool_calls, o padrao OpenAI e content nulo.
                    content=(result.text or None) if result.tool_calls else result.text,
                    tool_calls=result.tool_calls,
                ),
                finish_reason=result.finish_reason,
            )
        ],
        usage=Usage(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.prompt_tokens + result.completion_tokens,
        ),
    )


async def _sse(provider, body: ChatCompletionRequest, spec) -> AsyncIterator[str]:
    """Gera o corpo SSE no formato chat.completion.chunk."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    def emit(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield emit(stream_chunk(completion_id, spec.id, role="assistant", delta=""))

    try:
        async for piece in provider.stream(body, spec):
            yield emit(stream_chunk(completion_id, spec.id, delta=piece))
    except ProviderError as exc:
        # O status HTTP ja foi enviado (200); o erro vai como evento no fluxo.
        log.warning("Erro durante streaming em %s: %s", exc.provider, exc.message)
        yield emit(error_payload(exc.message, "provider_error", exc.provider))
        yield "data: [DONE]\n\n"
        return
    except Exception as exc:
        log.exception("Falha inesperada durante streaming")
        yield emit(error_payload(str(exc), "internal_error"))
        yield "data: [DONE]\n\n"
        return

    yield emit(stream_chunk(completion_id, spec.id, finish_reason="stop"))
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Interface web (montada por ultimo para nao capturar as rotas acima)
# ---------------------------------------------------------------------------

def _asset_version() -> str:
    """Hash curto do conteudo dos assets, usado para invalidar cache de CDN.

    Sem isso, um proxy/CDN na frente do servidor continua servindo o JS antigo
    depois de um deploy, e a interface quebra de forma silenciosa.
    """
    digest = hashlib.sha256()
    for nome in sorted(("chat.js", "style.css")):
        caminho = WEB_DIR / nome
        if caminho.is_file():
            digest.update(caminho.read_bytes())
    return digest.hexdigest()[:12]


if WEB_DIR.is_dir():
    _INDEX = (WEB_DIR / "index.html").read_text(encoding="utf-8").replace(
        "__ASSET_V__", _asset_version()
    )

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        # no-store no HTML: e ele que aponta para a versao atual dos assets.
        return HTMLResponse(_INDEX, headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def run() -> None:
    """Ponto de entrada para `python -m app.main`."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
