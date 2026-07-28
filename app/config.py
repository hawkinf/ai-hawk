"""Configuracao carregada de variaveis de ambiente / arquivo .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # Qualquer servico que fale o dialeto OpenAI: llama.cpp, vLLM, LM Studio,
    # litellm, text-generation-webui. Rodam na sua maquina, custo zero.
    ollama_base_url: str = "http://localhost:11434/v1"
    litellm_base_url: str = ""
    litellm_api_key: str = ""
    llamacpp_base_url: str = ""
    llamacpp_api_key: str = ""

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
