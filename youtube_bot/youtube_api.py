"""
youtube_bot.youtube_api
=======================
Todas as operações de I/O com a YouTube Data API v3.

Custo de quota (unidades) por chamada:
  - playlistItems.list / videos.list → 1
  - playlistItems.insert             → 50
Quota padrão do projeto: 10.000 unidades/dia.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from googleapiclient.errors import HttpError

from youtube_bot import config

logger = logging.getLogger(__name__)

# Motivos de erro que indicam falta de quota — não adianta continuar a execução
_QUOTA_REASONS = frozenset(
    {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}
)


class QuotaExceededError(Exception):
    """A quota da YouTube Data API acabou; a execução deve parar."""


def is_quota_error(err: HttpError) -> bool:
    """
    Distingue 403 de quota de outros 403 (ex.: playlist sem permissão).

    O YouTube devolve 403 tanto para quota estourada quanto para acesso
    negado; só o campo `reason` do corpo diferencia os dois casos.
    """
    status = getattr(err.resp, "status", None)
    if status == 429:
        return True
    if status != 403:
        return False
    try:
        payload = json.loads(err.content.decode("utf-8"))
        reasons = {e.get("reason") for e in payload["error"].get("errors", [])}
    except (ValueError, KeyError, AttributeError, TypeError):
        return False
    return bool(reasons & _QUOTA_REASONS)


def _execute(request: Any) -> dict[str, Any]:
    """
    Executa uma requisição com retry automático (5xx/429, backoff exponencial)
    e converte erros de quota em QuotaExceededError.
    """
    try:
        return request.execute(num_retries=config.API_NUM_RETRIES)
    except HttpError as err:
        if is_quota_error(err):
            raise QuotaExceededError(str(err)) from err
        raise


# ============================================================
# READ
# ============================================================

def get_uploads_playlist_id(channel_id: str) -> str:
    """
    Retorna o ID da playlist de uploads de um canal SEM chamar a API.

    A playlist de uploads de um canal "UCxxxx" é sempre "UUxxxx" — isso
    economiza uma chamada channels.list por canal a cada execução.
    """
    if not channel_id.startswith("UC"):
        raise ValueError(f"channel_id inválido (esperado prefixo 'UC'): {channel_id}")
    return "UU" + channel_id[2:]


def list_latest_uploads(
    youtube: Any,
    uploads_playlist_id: str,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    """Retorna os vídeos mais recentes de uma playlist de uploads."""
    resp = _execute(
        youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=max_results or config.UPLOADS_LOOKBACK,
        )
    )
    return resp.get("items", [])


def _paginate_ids(make_request: Any, extract_id: Any) -> set[str]:
    """Percorre todas as páginas de um endpoint de listagem e coleta IDs."""
    ids: set[str] = set()
    page_token: str | None = None
    while True:
        resp = _execute(make_request(page_token))
        ids.update(extract_id(item) for item in resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            return ids


def get_liked_videos(youtube: Any, state: dict[str, Any]) -> set[str]:
    """
    Retorna o conjunto de IDs de todos os vídeos curtidos pelo usuário.

    Usa cache com TTL de LIKED_CACHE_TTL_HOURS armazenado no state.
    """
    cache = state.get("_liked_videos_cache", {})
    cached_at_str: str | None = cache.get("cached_at")

    if cached_at_str:
        try:
            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        except ValueError:
            age_hours = float("inf")
        if age_hours < config.LIKED_CACHE_TTL_HOURS:
            cached_ids: list[str] = cache.get("ids", [])
            logger.info(
                "Cache de liked videos válido (%d vídeos, %.1fh atrás).",
                len(cached_ids),
                age_hours,
            )
            return set(cached_ids)

    logger.info("Buscando liked videos da API do YouTube...")
    liked = _paginate_ids(
        lambda token: youtube.videos().list(
            part="id", myRating="like", maxResults=50, pageToken=token
        ),
        lambda item: item["id"],
    )

    state["_liked_videos_cache"] = {
        "ids": sorted(liked),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("Liked videos carregados da API: %d", len(liked))
    return liked


def get_all_playlist_video_ids(youtube: Any, playlist_ids: set[str]) -> set[str]:
    """Retorna todos os video_ids já presentes nas playlists monitoradas."""
    all_videos: set[str] = set()
    for playlist_id in playlist_ids:
        all_videos |= _paginate_ids(
            lambda token, pid=playlist_id: youtube.playlistItems().list(
                part="contentDetails", playlistId=pid, maxResults=50, pageToken=token
            ),
            lambda item: item["contentDetails"]["videoId"],
        )
    return all_videos


# ============================================================
# WRITE
# ============================================================

def add_video_to_playlist(youtube: Any, playlist_id: str, video_id: str) -> None:
    """Insere um vídeo em uma playlist (custa 50 unidades de quota)."""
    _execute(
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        )
    )
