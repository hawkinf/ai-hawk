"""Adaptador generico para qualquer API compativel com a da OpenAI.

Cobre Ollama, Groq, OpenRouter, Cerebras, Google (endpoint OpenAI-compat) e a
propria OpenAI. Todos falam o mesmo dialeto em /chat/completions e /models.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from app.providers.base import ChatProvider, ChatResult, ModelSpec, ProviderError
from app.schemas import ChatCompletionRequest

log = logging.getLogger(__name__)


class OpenAICompatProvider(ChatProvider):
    """Cliente para endpoints no formato OpenAI.

    Parametros
    ----------
    discover:
        Quando True, os modelos vem de GET /models em tempo de execucao.
        Quando False, usa `static_models`.
    model_filter:
        Filtro aplicado sobre os ids retornados na descoberta (usado pelo
        OpenRouter para expor somente modelos ":free").
    """

    def __init__(
        self,
        name: str,
        *,
        base_url: str,
        api_key: str,
        tier: str = "free",
        discover: bool = False,
        static_models: list[ModelSpec] | None = None,
        model_filter: Callable[[str], bool] | None = None,
        default_context: int | None = None,
        default_max_output: int = 4096,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 300.0,
        requires_key: bool = True,
    ) -> None:
        self.name = name
        self.tier = tier
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.discover = discover
        self.static_models = static_models or []
        self.model_filter = model_filter
        self.default_context = default_context
        self.default_max_output = default_max_output
        self.extra_headers = extra_headers or {}
        self.timeout = timeout
        self.requires_key = requires_key
        self._client: httpx.AsyncClient | None = None

    # --- infraestrutura ---------------------------------------------------

    @property
    def enabled(self) -> bool:
        if self.requires_key:
            return bool(self.api_key)
        return bool(self.base_url)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json", **self.extra_headers}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=httpx.Timeout(self.timeout, connect=15.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _fail(self, exc: Exception) -> ProviderError:
        """Traduz uma excecao httpx em ProviderError com status util."""
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            detail = _extract_error(exc.response)
            # Repassa 4xx e os 5xx que carregam significado para o cliente:
            # 503 = tente de novo em instantes, 504 = demorou demais. Colapsar
            # esses em 502 faria o cliente achar que o gateway esta quebrado.
            out = status if (400 <= status < 500 or status in (503, 504)) else 502
            return ProviderError(self.name, f"[{self.name}] {detail}", out)
        if isinstance(exc, httpx.ConnectError):
            return ProviderError(
                self.name,
                f"[{self.name}] nao foi possivel conectar em {self.base_url}. "
                "O servico esta rodando?",
                503,
            )
        if isinstance(exc, httpx.TimeoutException):
            return ProviderError(self.name, f"[{self.name}] tempo limite excedido", 504)
        return ProviderError(self.name, f"[{self.name}] {exc}", 502)

    # --- catalogo ---------------------------------------------------------

    async def list_models(self) -> list[ModelSpec]:
        if not self.discover:
            return list(self.static_models)
        try:
            resp = await self._http().get("/models")
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except Exception as exc:  # noqa: BLE001 - catalogo nao pode derrubar a rota
            log.warning("Descoberta de modelos falhou em %s: %s", self.name, exc)
            return []

        specs: list[ModelSpec] = []
        for item in data:
            upstream = item.get("id")
            if not upstream:
                continue
            if self.model_filter and not self.model_filter(upstream):
                continue
            specs.append(
                ModelSpec(
                    id=f"{self.name}/{upstream}",
                    provider=self.name,
                    upstream_id=upstream,
                    tier=self.tier,
                    label=_pretty(upstream),
                    context_window=item.get("context_length") or self.default_context,
                    max_output_tokens=self.default_max_output,
                )
            )
        specs.sort(key=lambda s: s.id)
        return specs

    # --- inferencia -------------------------------------------------------

    def _payload(
        self, req: ChatCompletionRequest, spec: ModelSpec, *, stream: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": spec.upstream_id,
            "messages": [m.model_dump(exclude_none=True) for m in req.messages],
            "stream": stream,
            "max_tokens": req.max_tokens or spec.max_output_tokens,
        }
        if spec.supports_sampling:
            if req.temperature is not None:
                body["temperature"] = req.temperature
            if req.top_p is not None:
                body["top_p"] = req.top_p
        if req.stop:
            body["stop"] = req.stop
        # Function calling: repassado como veio. O ai-hawk nao interpreta o
        # schema - quem valida e o modelo. Sem isto o campo sumia aqui e o
        # modelo respondia como se nao tivesse ferramenta alguma.
        if req.tools:
            body["tools"] = req.tools
            if req.tool_choice is not None:
                body["tool_choice"] = req.tool_choice
        return body

    async def chat(self, req: ChatCompletionRequest, spec: ModelSpec) -> ChatResult:
        try:
            resp = await self._http().post(
                "/chat/completions", json=self._payload(req, spec, stream=False)
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise self._fail(exc) from exc

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(self.name, f"[{self.name}] resposta sem choices", 502)

        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        return ChatResult(
            text=message.get("content") or "",
            tool_calls=message.get("tool_calls") or None,
            finish_reason=choices[0].get("finish_reason") or "stop",
            prompt_tokens=usage.get("prompt_tokens", 0) or 0,
            completion_tokens=usage.get("completion_tokens", 0) or 0,
        )

    async def stream(
        self, req: ChatCompletionRequest, spec: ModelSpec
    ) -> AsyncIterator[str | dict[str, Any]]:
        payload = self._payload(req, spec, stream=True)
        try:
            async with self._http().stream(
                "POST", "/chat/completions", json=payload
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    resp.raise_for_status()
                async for line in resp.aiter_lines():
                    chunk = _parse_sse_line(line)
                    if chunk is None:
                        continue
                    if chunk is _DONE:
                        return
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    escolha = choices[0]
                    delta = escolha.get("delta") or {}
                    # Os fragmentos de tool_call vao crus: e o cliente que
                    # remonta nome e argumentos pedaco por pedaco.
                    if delta.get("tool_calls"):
                        yield {"tool_calls": delta["tool_calls"]}
                    conteudo = delta.get("content")
                    if conteudo:
                        yield conteudo
                    if escolha.get("finish_reason"):
                        yield {"finish_reason": escolha["finish_reason"]}
        except Exception as exc:
            raise self._fail(exc) from exc


# --- helpers ---------------------------------------------------------------

_DONE: Any = object()


def _parse_sse_line(line: str) -> dict[str, Any] | Any | None:
    """Converte uma linha SSE em dict. Devolve None para linhas ignoraveis."""
    line = line.strip()
    if not line or line.startswith(":") or not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return _DONE
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def _extract_error(resp: httpx.Response) -> str:
    """Extrai a mensagem de erro do corpo, seja qual for o formato do provedor."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return (resp.text or f"HTTP {resp.status_code}")[:400]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:400]
        if isinstance(err, str):
            return err[:400]
        if body.get("message"):
            return str(body["message"])[:400]
    return json.dumps(body)[:400]


def _pretty(model_id: str) -> str:
    """Nome legivel a partir do id do modelo."""
    return model_id.split("/")[-1].replace(":free", " (free)")
