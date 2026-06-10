"""
youtube_bot.auth
================
Autenticação OAuth2 com a Google API.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from youtube_bot.config import SCOPES, get_required_env

logger = logging.getLogger(__name__)


def load_creds(state: dict[str, Any]) -> Credentials:
    """
    Carrega e valida as credenciais OAuth2 a partir da env var GOOGLE_TOKEN_JSON.

    Quando o access_token está expirado, renova via refresh_token e registra
    o timestamp do refresh no state — permitindo rastrear renovações em produção.

    Parâmetros
    ----------
    state : dict
        Estado global do bot. Usado para registrar metadados do refresh.

    Returns
    -------
    Credentials
        Credenciais válidas e prontas para uso.

    Raises
    ------
    RuntimeError
        Se as credenciais estiverem inválidas e não houver refresh_token
        (exige re-autenticação manual e atualização do Secret).
    """
    info = json.loads(get_required_env("GOOGLE_TOKEN_JSON"))
    creds = Credentials.from_authorized_user_info(info, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info("Access token expirado — renovando via refresh token...")
            creds.refresh(Request())
            state["_last_token_refresh"] = datetime.now(timezone.utc).isoformat()
            logger.info("Token renovado com sucesso.")
        else:
            raise RuntimeError(
                "Credenciais OAuth2 inválidas e sem refresh_token disponível.\n"
                "Re-autentique localmente e atualize o Secret GOOGLE_TOKEN_JSON."
            )

    return creds
