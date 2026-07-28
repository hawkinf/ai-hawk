"""Adaptadores de provedores de LLM."""

from app.providers.base import (
    ChatProvider,
    ChatResult,
    ModelSpec,
    ProviderError,
)

__all__ = ["ChatProvider", "ChatResult", "ModelSpec", "ProviderError"]
