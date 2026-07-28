"""Autenticacao por API key nas rotas /v1/*."""

from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException, status

from app.config import get_settings

log = logging.getLogger(__name__)


async def require_api_key(authorization: str | None = Header(default=None)) -> str:
    """Valida o header `Authorization: Bearer <chave>`.

    Se HAWK_API_KEYS estiver vazio, a autenticacao fica desligada (modo dev
    local) e a dependencia passa direto.
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return "anonymous"

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="informe 'Authorization: Bearer <sua-chave>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[7:].strip()
    # compare_digest evita vazamento de informacao por tempo de comparacao.
    for known in settings.api_keys:
        if hmac.compare_digest(token, known):
            return token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="chave de API invalida",
        headers={"WWW-Authenticate": "Bearer"},
    )
