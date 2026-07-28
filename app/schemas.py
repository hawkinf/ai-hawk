"""Schemas Pydantic no formato da API da OpenAI.

Manter esse formato permite que qualquer SDK OpenAI (Python, C#, Dart, JS)
consuma este servidor apenas trocando a base_url.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Role = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    role: Role
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0, le=128_000)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    stop: list[str] | None = None

    @field_validator("messages")
    @classmethod
    def _must_have_non_system(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        if all(m.role == "system" for m in v):
            raise ValueError("messages precisa conter ao menos uma mensagem user/assistant")
        return v

    def system_prompt(self) -> str | None:
        """Concatena as mensagens de sistema (alguns provedores as tratam a parte)."""
        parts = [m.content for m in self.messages if m.role == "system"]
        return "\n\n".join(parts) if parts else None

    def conversation(self) -> list[ChatMessage]:
        """Mensagens sem os blocos de sistema."""
        return [m for m in self.messages if m.role != "system"]


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChoiceMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:24]}")
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[Choice]
    usage: Usage = Field(default_factory=Usage)


class ModelCard(BaseModel):
    """Item de GET /v1/models. Os campos extras sao especificos do ai-hawk."""

    id: str
    object: Literal["model"] = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str

    # Extensoes ai-hawk
    label: str
    provider: str
    tier: Literal["free", "paid"]
    available: bool = True
    context_window: int | None = None


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelCard]


class ErrorBody(BaseModel):
    message: str
    type: str
    code: str | None = None
    param: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


def error_payload(message: str, type_: str, code: str | None = None) -> dict[str, Any]:
    """Corpo de erro no formato OpenAI, para uso em JSONResponse."""
    return ErrorResponse(error=ErrorBody(message=message, type=type_, code=code)).model_dump()


def stream_chunk(
    completion_id: str,
    model: str,
    *,
    delta: str | None = None,
    role: str | None = None,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    """Monta um chunk SSE no formato chat.completion.chunk."""
    payload: dict[str, Any] = {}
    if role is not None:
        payload["role"] = role
    if delta is not None:
        payload["content"] = delta
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": payload, "finish_reason": finish_reason}],
    }
