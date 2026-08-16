"""Contrato comum a todos os provedores de LLM."""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.schemas import ChatCompletionRequest


class ProviderError(RuntimeError):
    """Falha na comunicacao com um provedor upstream.

    `status` e o codigo HTTP que o ai-hawk devera devolver ao cliente.
    """

    def __init__(self, provider: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.provider = provider
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Um modelo exposto pelo ai-hawk.

    `id` e o identificador publico (sempre "<provider>/<modelo>").
    `upstream_id` e o identificador esperado pelo provedor.
    """

    id: str
    provider: str
    upstream_id: str
    tier: str  # "free" | "paid"
    label: str
    context_window: int | None = None
    supports_sampling: bool = True
    max_output_tokens: int = 4096
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def is_free(self) -> bool:
        return self.tier == "free"


@dataclass(slots=True)
class ChatResult:
    """Resposta nao-streaming normalizada."""

    text: str
    finish_reason: str = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Preenchido quando o modelo pede execucao de ferramenta em vez de texto.
    tool_calls: list[dict[str, Any]] | None = None


class ChatProvider(abc.ABC):
    """Interface que todo adaptador de provedor implementa."""

    name: str
    tier: str = "free"

    @property
    @abc.abstractmethod
    def enabled(self) -> bool:
        """True quando o provedor esta configurado (chave presente, etc.)."""

    @abc.abstractmethod
    async def list_models(self) -> list[ModelSpec]:
        """Modelos oferecidos por este provedor."""

    @abc.abstractmethod
    async def chat(self, req: ChatCompletionRequest, spec: ModelSpec) -> ChatResult:
        """Completa a conversa de uma vez."""

    @abc.abstractmethod
    def stream(
        self, req: ChatCompletionRequest, spec: ModelSpec
    ) -> AsyncIterator[str | dict[str, Any]]:
        """Gera a resposta em pedacos.

        Emite `str` para texto. Para o que nao e texto - `tool_calls` e
        `finish_reason` - emite um dict com o delta cru no formato OpenAI.
        """

    async def aclose(self) -> None:
        """Libera recursos (conexoes HTTP)."""
        return None
