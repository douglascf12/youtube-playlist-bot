"""
youtube_bot.processor
=====================
Lógica de negócio do bot: filtragem de vídeos e orquestração do fluxo principal.

Responsabilidades:
  - is_recent          → regra de negócio: filtro por data de publicação
  - process_channel    → processa um canal e insere vídeos elegíveis
  - main               → entrypoint orquestrador do bot
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from youtube_bot.auth import load_creds
from youtube_bot.config import (
    MAX_VIDEO_AGE_DAYS,
    MAX_VIDEOS_PER_CHANNEL,
    get_required_env,
)
from youtube_bot.state import (
    is_processed,
    load_state,
    mark_processed,
    save_state,
)
from youtube_bot.youtube_api import (
    add_video_to_playlist,
    get_all_playlist_video_ids,
    get_liked_videos,
    get_uploads_playlist_id,
    list_latest_uploads,
)

logger = logging.getLogger(__name__)


def is_recent(published_at_iso: str) -> bool:
    """
    Retorna True se o vídeo foi publicado dentro da janela configurada.

    Parâmetros
    ----------
    published_at_iso : str
        Data de publicação no formato ISO 8601 (ex: "2026-01-15T12:00:00Z").
    """
    published = datetime.fromisoformat(published_at_iso.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - published <= timedelta(days=MAX_VIDEO_AGE_DAYS)


def process_channel(
    youtube: Any,
    channel_id: str,
    playlist_id: str,
    state: dict[str, Any],
    liked_videos: set[str],
    existing_playlist_videos: set[str],
) -> None:
    """
    Processa um canal: busca uploads recentes e adiciona os elegíveis à playlist.

    Critérios de elegibilidade (todos devem ser verdadeiros):
    - Não foi processado anteriormente (state)
    - Não está em nenhuma playlist monitorada
    - Foi publicado há menos de MAX_VIDEO_AGE_DAYS dias
    - Não foi curtido pelo usuário autenticado

    Limita inserções a MAX_VIDEOS_PER_CHANNEL por execução (proteção de quota).
    Em caso de erro HTTP 403 (quota excedida), salva o state e encerra o canal.

    Parâmetros
    ----------
    youtube : Resource
        Cliente autenticado da YouTube Data API.
    channel_id : str
        ID do canal a processar.
    playlist_id : str
        ID da playlist de destino para os vídeos elegíveis.
    state : dict
        State global do bot (modificado in-place).
    liked_videos : set[str]
        IDs de vídeos curtidos pelo usuário autenticado.
    existing_playlist_videos : set[str]
        IDs de vídeos já presentes em qualquer playlist monitorada.
    """
    uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id)
    items = list_latest_uploads(youtube, uploads_playlist_id)
    added = 0

    for item in items:
        snippet = item["snippet"]
        video_id: str = snippet["resourceId"]["videoId"]
        published_at: str = snippet["publishedAt"]

        # Já processado anteriormente
        if is_processed(state, channel_id, video_id):
            continue

        # Já está em alguma das playlists monitoradas
        if video_id in existing_playlist_videos:
            logger.info(f"[{channel_id}] Ignorado (já em playlist): {video_id}")
            mark_processed(state, channel_id, video_id)
            continue

        # Vídeo antigo demais
        if not is_recent(published_at):
            logger.info(
                f"[{channel_id}] Ignorado (antigo, publicado em {published_at[:10]}): {video_id}"
            )
            mark_processed(state, channel_id, video_id)
            continue

        # Vídeo curtido pelo usuário
        if video_id in liked_videos:
            logger.info(f"[{channel_id}] Ignorado (curtido): {video_id}")
            mark_processed(state, channel_id, video_id)
            continue

        # Elegível — inserir na playlist
        try:
            add_video_to_playlist(youtube, playlist_id, video_id)
            logger.info(f"[{channel_id}] Adicionado em {playlist_id}: {video_id}")

            mark_processed(state, channel_id, video_id)
            existing_playlist_videos.add(video_id)

            added += 1
            if added >= MAX_VIDEOS_PER_CHANNEL:
                break

        except HttpError as e:
            if e.resp.status == 403:
                logger.warning(
                    f"[{channel_id}] Quota da API excedida (HTTP 403). "
                    "Encerrando processamento deste canal."
                )
                # Salva explicitamente: o return a seguir impede o finally
                # do main() de alcançar este ponto de forma ordenada
                save_state(state)
                return
            logger.error(
                f"[{channel_id}] HttpError {e.resp.status} ao adicionar {video_id}: {e}"
            )
            raise

    if added == 0:
        logger.info(f"[{channel_id}] Nenhum vídeo elegível encontrado")
    else:
        logger.info(
            f"[{channel_id}] {added} vídeo(s) adicionado(s) à playlist {playlist_id}"
        )


def main() -> None:
    """
    Entrypoint principal do bot.

    Fluxo:
    1. Carrega mapa de canais → playlists (env var YT_CHANNEL_PLAYLIST_MAP)
    2. Carrega state persistido em disco
    3. Autentica com a API do YouTube (com renovação automática de token)
    4. Busca liked videos (com cache TTL no state)
    5. Carrega IDs de vídeos já presentes nas playlists (deduplicação)
    6. Itera sobre cada canal e processa uploads recentes
    7. Persiste o state no finally (garante gravação mesmo em exceções)
    """
    logger.info("=" * 60)
    logger.info("Iniciando youtube-playlist-bot")
    logger.info("=" * 60)

    channel_playlist_map: dict[str, str] = json.loads(
        get_required_env("YT_CHANNEL_PLAYLIST_MAP")
    )
    logger.info(f"Canais configurados: {len(channel_playlist_map)}")

    state = load_state()

    try:
        creds = load_creds(state)
        youtube = build("youtube", "v3", credentials=creds)

        liked_videos = get_liked_videos(youtube, state)

        playlist_ids = set(channel_playlist_map.values())
        existing_playlist_videos = get_all_playlist_video_ids(youtube, playlist_ids)
        logger.info(f"Vídeos já presentes nas playlists: {len(existing_playlist_videos)}")

        for channel_id, playlist_id in channel_playlist_map.items():
            logger.info(f"Processando canal: {channel_id} → playlist: {playlist_id}")
            process_channel(
                youtube=youtube,
                channel_id=channel_id,
                playlist_id=playlist_id,
                state=state,
                liked_videos=liked_videos,
                existing_playlist_videos=existing_playlist_videos,
            )

        logger.info("Execução concluída.")

    finally:
        save_state(state)
        logger.info("State salvo.")
