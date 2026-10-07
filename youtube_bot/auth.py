"""
youtube_bot.auth
================
Autenticação OAuth2 com a Google API.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from google.auth.exceptions import RefreshError
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
    try:
        info = json.loads(get_required_env("GOOGLE_TOKEN_JSON"))
    except json.JSONDecodeError:
        # Nunca incluir o conteúdo do secret na mensagem de erro
        raise RuntimeError("GOOGLE_TOKEN_JSON não é um JSON válido.") from None

    creds = Credentials.from_authorized_user_info(info, SCOPES)

    if not creds.valid:
        if creds.refresh_token:
            logger.info("Access token ausente/expirado — renovando via refresh token...")
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                # invalid_grant: refresh token revogado ou expirado. Com o app
                # OAuth em modo "Testing", o Google expira o token em 7 dias.
                raise RuntimeError(
                    "Falha ao renovar o token OAuth2 (refresh token revogado ou expirado). "
                    "Rode `python bootstrap.py` e atualize o Secret GOOGLE_TOKEN_JSON. "
                    "Dica: publique o app OAuth ('In production') para o token não expirar "
                    f"em 7 dias. Detalhe: {exc}"
                ) from exc
            state["_last_token_refresh"] = datetime.now(timezone.utc).isoformat()
            logger.info("Token renovado com sucesso.")
        else:
            raise RuntimeError(
                "Credenciais OAuth2 inválidas e sem refresh_token disponível.\n"
                "Re-autentique localmente e atualize o Secret GOOGLE_TOKEN_JSON."
            )

    return creds
