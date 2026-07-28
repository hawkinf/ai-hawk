"""Fixtures compartilhadas: um provedor stub que nao faz chamadas de rede."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.providers.base import ChatProvider, ChatResult, ModelSpec
from app.schemas import ChatCompletionRequest

FREE_MODEL = "stub/eco-free"
PAID_MODEL = "stub/eco-paid"


class StubProvider(ChatProvider):
    """Ecoa a ultima mensagem do usuario. Deterministico e offline."""

    name = "stub"
    tier = "free"

    @property
    def enabled(self) -> bool:
        return True

    async def list_models(self) -> list[ModelSpec]:
        return [
            ModelSpec(
                id=FREE_MODEL,
                provider="stub",
                upstream_id="eco-free",
                tier="free",
                label="Eco (gratuito)",
                context_window=8192,
            ),
            ModelSpec(
                id=PAID_MODEL,
                provider="stub",
                upstream_id="eco-paid",
                tier="paid",
                label="Eco (pago)",
                context_window=8192,
            ),
        ]

    def _echo(self, req: ChatCompletionRequest) -> str:
        return f"eco: {req.conversation()[-1].content}"

    async def chat(self, req: ChatCompletionRequest, spec: ModelSpec) -> ChatResult:
        return ChatResult(text=self._echo(req), prompt_tokens=7, completion_tokens=5)

    async def stream(
        self, req: ChatCompletionRequest, spec: ModelSpec
    ) -> AsyncIterator[str]:
        for word in self._echo(req).split(" "):
            yield word + " "


def _make_client(monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    from app.config import get_settings
    from app.registry import Registry

    for key, value in env.items():
        monkeypatch.setenv(key.upper(), value)
    monkeypatch.setenv("HAWK_API_KEYS", env.get("hawk_api_keys", ""))
    get_settings.cache_clear()

    original_build = Registry._build

    def patched_build(self: Registry) -> None:
        original_build(self)
        self.providers["stub"] = StubProvider()

    monkeypatch.setattr(Registry, "_build", patched_build)

    from app.main import app

    return TestClient(app)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Servidor com guarda de custo ATIVA e autenticacao desligada."""
    with _make_client(monkeypatch, allow_paid_models="false") as c:
        yield c


@pytest.fixture
def paid_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Servidor com modelos pagos liberados."""
    with _make_client(monkeypatch, allow_paid_models="true") as c:
        yield c


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Servidor exigindo a chave 'chave-secreta'."""
    with _make_client(
        monkeypatch, allow_paid_models="false", hawk_api_keys="chave-secreta"
    ) as c:
        yield c
