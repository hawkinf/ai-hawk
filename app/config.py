"""Configuracao carregada de variaveis de ambiente / arquivo .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LocalBackend(BaseModel):
    """Servico local que fala o dialeto OpenAI.

    Cobre litellm, llama.cpp --server, vLLM, LM Studio, gateways proprios.
    Sempre tratado como gratuito: roda na sua propria maquina.

    `name` vira o prefixo do id do modelo (ex.: name="hawk" -> "hawk/gemma4"),
    entao escolha algo curto e descritivo.
    """

    name: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")
    base_url: str
    api_key: str = ""


class Settings(BaseSettings):
    """Configuracao da aplicacao.

    Todos os campos vem de variaveis de ambiente (case-insensitive) ou do .env.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Servidor ---------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "INFO"
    request_timeout: float = 300.0

    # --- Autenticacao da API local ---------------------------------------
    hawk_api_keys: str = ""

    # --- Guarda de custo --------------------------------------------------
    allow_paid_models: bool = False

    # --- Backends locais auto-hospedados (sempre gratuitos) ---------------
    ollama_base_url: str = "http://localhost:11434/v1"

    # Lista JSON de servicos locais OpenAI-compat. Exemplo no .env:
    #   LOCAL_BACKENDS=[{"name":"hawk","base_url":"http://127.0.0.1:8096/v1","api_key":"..."}]
    local_backends: list[LocalBackend] = []

    # --- Provedores gratuitos na nuvem (free tier) -----------------------
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    cerebras_api_key: str = ""
    google_api_key: str = ""

    # --- Provedores pagos -------------------------------------------------
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # --- Derivados --------------------------------------------------------
    @property
    def api_keys(self) -> set[str]:
        """Conjunto de chaves aceitas na API. Vazio = autenticacao desligada."""
        return {k.strip() for k in self.hawk_api_keys.split(",") if k.strip()}

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instancia unica de Settings (cacheada)."""
    return Settings()
