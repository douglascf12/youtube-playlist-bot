"""
youtube_bot.youtube_api
=======================
Todas as operações de I/O com a YouTube Data API v3.

Funções de leitura:
  - get_uploads_playlist_id   → ID da playlist de uploads de um canal
  - list_latest_uploads       → vídeos mais recentes de uma playlist
  - get_liked_videos          → IDs de vídeos curtidos (com cache no state)
  - get_all_playlist_video_ids → IDs de todos vídeos em playlists monitoradas

Funções de escrita:
  - add_video_to_playlist     → insere um vídeo em uma playlist
"""

import logging
from datetime import datetime, timezone
from typing import Any

from youtube_bot.config import LIKED_CACHE_TTL_HOURS

logger = logging.getLogger(__name__)


# ============================================================
# READ
# ============================================================

def get_uploads_playlist_id(youtube: Any, channel_id: str) -> str:
    """
    Retorna o ID da playlist de uploads de um canal do YouTube.

    Parâmetros
    ----------
    youtube : Resource
        Cliente autenticado da YouTube Data API.
    channel_id : str
        ID do canal (ex: "UCxxxxxx").

    Raises
    ------
    RuntimeError
        Se o canal não for encontrado na API.
    """
    resp = youtube.channels().list(
        part="contentDetails",
        id=channel_id,
    ).execute()

    items = resp.get("items", [])
    if not items:
        raise RuntimeError(f"Canal não encontrado na API do YouTube: {channel_id}")

    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def list_latest_uploads(
    youtube: Any,
    uploads_playlist_id: str,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """
    Retorna os vídeos mais recentes de uma playlist de uploads.

    Parâmetros
    ----------
    youtube : Resource
        Cliente autenticado da YouTube Data API.
    uploads_playlist_id : str
        ID da playlist de uploads do canal.
    max_results : int
        Máximo de itens a retornar (padrão: 10).
    """
    resp = youtube.playlistItems().list(
        part="snippet",
        playlistId=uploads_playlist_id,
        maxResults=max_results,
    ).execute()
    return resp.get("items", [])


def get_liked_videos(youtube: Any, state: dict[str, Any]) -> set[str]:
    """
    Retorna o conjunto de IDs de todos os vídeos curtidos pelo usuário autenticado.

    Utiliza cache com TTL de LIKED_CACHE_TTL_HOURS horas armazenado no state,
    evitando chamadas desnecessárias à API em execuções próximas.

    Parâmetros
    ----------
    youtube : Resource
        Cliente autenticado da YouTube Data API.
    state : dict
        State global — usado para leitura e escrita do cache de liked videos.
    """
    cache = state.get("_liked_videos_cache", {})
    cached_at_str: str | None = cache.get("cached_at")

    if cached_at_str:
        cached_at = datetime.fromisoformat(cached_at_str)
        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        if age_hours < LIKED_CACHE_TTL_HOURS:
            cached_ids: list[str] = cache.get("ids", [])
            logger.info(
                f"Cache de liked videos válido ({len(cached_ids)} vídeos, "
                f"{age_hours:.1f}h atrás). Pulando chamada à API."
            )
            return set(cached_ids)

    logger.info("Buscando liked videos da API do YouTube...")
    liked: set[str] = set()
    page_token: str | None = None

    while True:
        resp = youtube.videos().list(
            part="id",
            myRating="like",
            maxResults=50,
            pageToken=page_token,
        ).execute()

        for item in resp.get("items", []):
            liked.add(item["id"])

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # Persistir cache no state para próximas execuções
    state["_liked_videos_cache"] = {
        "ids": list(liked),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(f"Liked videos carregados da API: {len(liked)}")
    return liked


def get_all_playlist_video_ids(
    youtube: Any,
    playlist_ids: set[str],
) -> set[str]:
    """
    Retorna um set com todos os video_ids já presentes nas playlists monitoradas.

    Usado para deduplicação antes de tentar inserir um vídeo: se o ID já estiver
    no set, o vídeo não será re-inserido independentemente do state local.

    Parâmetros
    ----------
    youtube : Resource
        Cliente autenticado da YouTube Data API.
    playlist_ids : set[str]
        IDs de todas as playlists monitoradas pelo bot.
    """
    all_videos: set[str] = set()

    for playlist_id in playlist_ids:
        page_token: str | None = None

        while True:
            resp = youtube.playlistItems().list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            ).execute()

            for item in resp.get("items", []):
                all_videos.add(item["contentDetails"]["videoId"])

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    return all_videos


# ============================================================
# WRITE
# ============================================================

def add_video_to_playlist(youtube: Any, playlist_id: str, video_id: str) -> None:
    """
    Insere um vídeo em uma playlist via YouTube Data API v3.

    Parâmetros
    ----------
    youtube : Resource
        Cliente autenticado da YouTube Data API.
    playlist_id : str
        ID da playlist de destino.
    video_id : str
        ID do vídeo a inserir.
    """
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id,
                },
            }
        },
    ).execute()
