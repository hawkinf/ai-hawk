"""Adaptador nativo da Anthropic (Claude).

Usa o SDK oficial `anthropic`. Este provedor e PAGO: so aparece no catalogo
quando ALLOW_PAID_MODELS=true e ANTHROPIC_API_KEY esta preenchida.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from app.providers.base import ChatProvider, ChatResult, ModelSpec, ProviderError
from app.schemas import ChatCompletionRequest

log = logging.getLogger(__name__)

PROVIDER = "anthropic"

# A familia 5 rejeita temperature/top_p (HTTP 400) - por isso supports_sampling=False.
_CATALOG: list[dict[str, Any]] = [
    {
        "id": "claude-opus-5",
        "label": "Claude Opus 5",
        "context": 1_000_000,
        "max_output": 16_000,
        "sampling": False,
    },
    {
        "id": "claude-sonnet-5",
        "label": "Claude Sonnet 5",
        "context": 1_000_000,
        "max_output": 16_000,
        "sampling": False,
    },
    {
        "id": "claude-haiku-4-5",
        "label": "Claude Haiku 4.5",
        "context": 200_000,
        "max_output": 8_000,
        "sampling": True,
    },
]


class AnthropicProvider(ChatProvider):
    name = PROVIDER
    tier = "paid"

    def __init__(self, api_key: str, *, timeout: float = 300.0) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _sdk(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependencia opcional
                raise ProviderError(
                    self.name,
                    "pacote 'anthropic' nao instalado (pip install anthropic)",
                    501,
                ) from exc
            self._client = anthropic.AsyncAnthropic(
                api_key=self.api_key, timeout=self.timeout
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def list_models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id=f"{PROVIDER}/{m['id']}",
                provider=PROVIDER,
                upstream_id=m["id"],
                tier="paid",
                label=m["label"],
                context_window=m["context"],
                max_output_tokens=m["max_output"],
                supports_sampling=m["sampling"],
            )
            for m in _CATALOG
        ]

    # --- inferencia -------------------------------------------------------

    def _kwargs(self, req: ChatCompletionRequest, spec: ModelSpec) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": spec.upstream_id,
            "max_tokens": req.max_tokens or spec.max_output_tokens,
            "messages": [m.model_dump() for m in req.conversation()],
        }
        system = req.system_prompt()
        if system:
            kwargs["system"] = system
        if spec.supports_sampling and req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.stop:
            kwargs["stop_sequences"] = req.stop
        return kwargs

    async def chat(self, req: ChatCompletionRequest, spec: ModelSpec) -> ChatResult:
        try:
            msg = await self._sdk().messages.create(**self._kwargs(req, spec))
        except Exception as exc:
            raise _translate(exc) from exc

        if msg.stop_reason == "refusal":
            raise ProviderError(
                self.name, "o modelo recusou a solicitacao por politica de seguranca", 403
            )

        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return ChatResult(
            text=text,
            finish_reason=_finish_reason(msg.stop_reason),
            prompt_tokens=msg.usage.input_tokens or 0,
            completion_tokens=msg.usage.output_tokens or 0,
        )

    async def stream(
        self, req: ChatCompletionRequest, spec: ModelSpec
    ) -> AsyncIterator[str]:
        try:
            async with self._sdk().messages.stream(**self._kwargs(req, spec)) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as exc:
            raise _translate(exc) from exc


def _finish_reason(stop_reason: str | None) -> str:
    return "length" if stop_reason == "max_tokens" else "stop"


def _translate(exc: Exception) -> ProviderError:
    """Converte excecoes do SDK Anthropic em ProviderError."""
    if isinstance(exc, ProviderError):
        return exc
    status = getattr(exc, "status_code", None)
    message = getattr(exc, "message", None) or str(exc)
    if isinstance(status, int) and 400 <= status < 500:
        return ProviderError(PROVIDER, f"[anthropic] {message}", status)
    return ProviderError(PROVIDER, f"[anthropic] {message}", 502)
