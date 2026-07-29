"""Testes do registro de provedores e da configuracao de backends locais."""

from __future__ import annotations

import json

import pytest

from app.config import LocalBackend, Settings, get_settings
from app.registry import Registry


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    get_settings.cache_clear()
    for chave, valor in env.items():
        monkeypatch.setenv(chave.upper(), valor)
    return Settings(_env_file=None)


def test_local_backends_vem_de_json_no_env(monkeypatch):
    s = _settings(
        monkeypatch,
        local_backends=json.dumps(
            [
                {"name": "hawk", "base_url": "http://127.0.0.1:8096/v1", "api_key": "t"},
                {"name": "litellm", "base_url": "http://127.0.0.1:4000/v1"},
            ]
        ),
    )
    assert [b.name for b in s.local_backends] == ["hawk", "litellm"]
    assert s.local_backends[0].api_key == "t"
    assert s.local_backends[1].api_key == ""


def test_cada_backend_local_vira_um_provedor(monkeypatch):
    s = _settings(
        monkeypatch,
        ollama_base_url="",
        local_backends=json.dumps(
            [
                {"name": "hawk", "base_url": "http://127.0.0.1:8096/v1"},
                {"name": "litellm", "base_url": "http://127.0.0.1:4000/v1"},
            ]
        ),
    )
    reg = Registry(s)
    assert "hawk" in reg.providers
    assert "litellm" in reg.providers
    assert reg.providers["hawk"].tier == "free"  # local nunca e pago


def test_nome_duplicado_nao_derruba_o_servidor(monkeypatch):
    """Colidir com um provedor de nuvem nao pode quebrar o start."""
    s = _settings(
        monkeypatch,
        ollama_base_url="",
        groq_api_key="chave-groq",
        local_backends=json.dumps(
            [{"name": "groq", "base_url": "http://127.0.0.1:9999/v1"}]
        ),
    )
    reg = Registry(s)
    # O primeiro registrado (o local) vence; o duplicado e ignorado com log de erro.
    assert reg.providers["groq"].base_url == "http://127.0.0.1:9999/v1"


def test_nome_de_backend_invalido_e_rejeitado():
    with pytest.raises(ValueError):
        LocalBackend(name="Nome Com Espaco", base_url="http://x/v1")
    with pytest.raises(ValueError):
        LocalBackend(name="", base_url="http://x/v1")


def test_backend_sem_url_fica_desativado(monkeypatch):
    s = _settings(
        monkeypatch,
        ollama_base_url="",
        local_backends=json.dumps([{"name": "vazio", "base_url": ""}]),
    )
    assert "vazio" not in Registry(s).providers
