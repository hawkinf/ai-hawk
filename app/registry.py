"""Registro de provedores e catalogo unificado de modelos.

Responsavel por:
  - instanciar apenas os provedores configurados;
  - juntar os catalogos num unico mapa id -> ModelSpec (com cache curto);
  - aplicar a GUARDA DE CUSTO antes de qualquer chamada paga.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.config import Settings
from app.providers.anthropic_p import AnthropicProvider
from app.providers.base import ChatProvider, ModelSpec, ProviderError
from app.providers.openai_compat import OpenAICompatProvider

log = logging.getLogger(__name__)

CATALOG_TTL_SECONDS = 120.0

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

# Catalogo estatico do Google. Flash tem free tier generoso no AI Studio;
# Pro fica marcado como pago por precaucao (guarda de custo bloqueia).
_GOOGLE_MODELS = [
    ("gemini-2.5-flash", "Gemini 2.5 Flash", "free", 1_000_000),
    ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", "free", 1_000_000),
    ("gemini-2.0-flash", "Gemini 2.0 Flash", "free", 1_000_000),
    ("gemini-2.5-pro", "Gemini 2.5 Pro", "paid", 1_000_000),
]


class ModelNotFound(LookupError):
    """O id de modelo pedido nao existe no catalogo."""


class PaidModelBlocked(PermissionError):
    """Modelo pago solicitado com a guarda de custo ativa."""


class Registry:
    """Mantem os provedores vivos e resolve ids de modelo."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.providers: dict[str, ChatProvider] = {}
        self._catalog: list[ModelSpec] = []  # visivel (respeita a guarda de custo)
        self._all: list[ModelSpec] = []  # completo (usado para resolver ids)
        self._by_id: dict[str, ModelSpec] = {}
        self._loaded_at: float = 0.0
        self._lock = asyncio.Lock()
        self._build()

    # --- construcao -------------------------------------------------------

    def _build(self) -> None:
        s = self.settings
        candidates: list[ChatProvider] = [
            # --- backends locais (custo zero: rodam na sua maquina) -------
            OpenAICompatProvider(
                "ollama",
                base_url=s.ollama_base_url,
                api_key="ollama",  # o Ollama ignora, mas o header precisa existir
                tier="free",
                discover=True,
                requires_key=False,
                default_max_output=4096,
                timeout=s.request_timeout,
            ),
            OpenAICompatProvider(
                "litellm",
                base_url=s.litellm_base_url,
                api_key=s.litellm_api_key,
                tier="free",
                discover=True,
                requires_key=False,
                default_max_output=8192,
                timeout=s.request_timeout,
            ),
            OpenAICompatProvider(
                "llamacpp",
                base_url=s.llamacpp_base_url,
                api_key=s.llamacpp_api_key,
                tier="free",
                discover=True,
                requires_key=False,
                default_max_output=8192,
                timeout=s.request_timeout,
            ),
            # --- free tiers na nuvem --------------------------------------
            OpenAICompatProvider(
                "groq",
                base_url="https://api.groq.com/openai/v1",
                api_key=s.groq_api_key,
                tier="free",
                discover=True,
                default_max_output=8192,
                timeout=s.request_timeout,
            ),
            OpenAICompatProvider(
                "openrouter",
                base_url="https://openrouter.ai/api/v1",
                api_key=s.openrouter_api_key,
                tier="free",
                discover=True,
                # So modelos gratuitos: o OpenRouter marca com o sufixo ":free".
                model_filter=lambda mid: mid.endswith(":free"),
                default_max_output=4096,
                extra_headers={"X-Title": "ai-hawk"},
                timeout=s.request_timeout,
            ),
            OpenAICompatProvider(
                "cerebras",
                base_url="https://api.cerebras.ai/v1",
                api_key=s.cerebras_api_key,
                tier="free",
                discover=True,
                default_max_output=8192,
                timeout=s.request_timeout,
            ),
            OpenAICompatProvider(
                "google",
                base_url=GOOGLE_BASE_URL,
                api_key=s.google_api_key,
                discover=False,
                static_models=[
                    ModelSpec(
                        id=f"google/{mid}",
                        provider="google",
                        upstream_id=mid,
                        tier=tier,
                        label=label,
                        context_window=ctx,
                        max_output_tokens=8192,
                    )
                    for mid, label, tier, ctx in _GOOGLE_MODELS
                ],
                timeout=s.request_timeout,
            ),
            # --- pagos ----------------------------------------------------
            AnthropicProvider(s.anthropic_api_key, timeout=s.request_timeout),
            OpenAICompatProvider(
                "openai",
                base_url="https://api.openai.com/v1",
                api_key=s.openai_api_key,
                tier="paid",
                discover=True,
                default_max_output=8192,
                timeout=s.request_timeout,
            ),
        ]

        # Provedores pagos configurados sao registrados mesmo com a guarda ativa:
        # assim resolve() devolve um 402 explicito em vez de um 404 confuso.
        for provider in candidates:
            if not provider.enabled:
                log.info("Provedor '%s' desativado (sem configuracao).", provider.name)
                continue
            self.providers[provider.name] = provider
            if provider.tier == "paid" and not s.allow_paid_models:
                log.info(
                    "Provedor '%s' configurado, porem BLOQUEADO (ALLOW_PAID_MODELS=false).",
                    provider.name,
                )
            else:
                log.info("Provedor '%s' ativo (tier=%s).", provider.name, provider.tier)

    # --- catalogo ---------------------------------------------------------

    async def catalog(self, *, refresh: bool = False) -> list[ModelSpec]:
        """Catalogo unificado, com cache de CATALOG_TTL_SECONDS."""
        fresh = (time.monotonic() - self._loaded_at) < CATALOG_TTL_SECONDS
        if self._catalog and fresh and not refresh:
            return self._catalog

        async with self._lock:
            fresh = (time.monotonic() - self._loaded_at) < CATALOG_TTL_SECONDS
            if self._catalog and fresh and not refresh:
                return self._catalog

            results = await asyncio.gather(
                *(p.list_models() for p in self.providers.values()),
                return_exceptions=True,
            )
            specs: list[ModelSpec] = []
            for provider, result in zip(self.providers.values(), results, strict=True):
                if isinstance(result, BaseException):
                    log.warning("Catalogo de '%s' falhou: %s", provider.name, result)
                    continue
                specs.extend(result)

            specs.sort(key=lambda s: (s.tier != "free", s.provider, s.id))
            self._all = specs
            self._by_id = {s.id: s for s in specs}
            self._catalog = (
                specs if self.settings.allow_paid_models else [s for s in specs if s.is_free]
            )
            self._loaded_at = time.monotonic()
            log.info(
                "Catalogo atualizado: %d modelos visiveis (%d no total).",
                len(self._catalog),
                len(specs),
            )
            return self._catalog

    async def resolve(self, model_id: str) -> ModelSpec:
        """Encontra o ModelSpec e aplica a guarda de custo.

        Aceita o id completo ("groq/llama-3.3-70b-versatile") ou, por
        conveniencia, apenas o nome do modelo quando nao houver ambiguidade.
        """
        await self.catalog()
        spec = self._by_id.get(model_id)

        if spec is None:
            matches = [s for s in self._all if s.upstream_id == model_id]
            if len(matches) == 1:
                spec = matches[0]
            elif len(matches) > 1:
                opts = ", ".join(s.id for s in matches)
                raise ModelNotFound(
                    f"'{model_id}' e ambiguo. Use o id completo: {opts}"
                )

        if spec is None:
            raise ModelNotFound(
                f"modelo '{model_id}' nao disponivel. Consulte GET /v1/models."
            )

        if spec.tier == "paid" and not self.settings.allow_paid_models:
            raise PaidModelBlocked(
                f"'{spec.id}' e um modelo pago e a guarda de custo esta ativa. "
                "Defina ALLOW_PAID_MODELS=true no .env para liberar."
            )
        return spec

    def provider_for(self, spec: ModelSpec) -> ChatProvider:
        provider = self.providers.get(spec.provider)
        if provider is None:  # pragma: no cover - catalogo e provedores sao coerentes
            raise ProviderError(spec.provider, f"provedor '{spec.provider}' indisponivel", 503)
        return provider

    async def aclose(self) -> None:
        for provider in self.providers.values():
            try:
                await provider.aclose()
            except Exception as exc:  # noqa: BLE001
                log.warning("Falha ao fechar '%s': %s", provider.name, exc)
