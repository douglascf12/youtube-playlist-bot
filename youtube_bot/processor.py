"""
youtube_bot.processor
=====================
Lógica de negócio do bot: filtragem de vídeos e orquestração do fluxo principal.

Responsabilidades:
  - is_recent          → regra de negócio: filtro por data de publicação
  - process_channel    → processa um canal e insere vídeos elegíveis
  - run                → orquestra a execução e devolve um RunReport
  - main               → entrypoint: executa, persiste state e define exit code
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from googleapiclient.discovery import build

from youtube_bot import config
from youtube_bot.auth import load_creds
from youtube_bot.state import is_processed, load_state, mark_processed, save_state
from youtube_bot.youtube_api import (
    QuotaExceededError,
    add_video_to_playlist,
    get_all_playlist_video_ids,
    get_liked_videos,
    get_uploads_playlist_id,
    list_latest_uploads,
)

logger = logging.getLogger(__name__)


# ============================================================
# Modelos
# ============================================================

@dataclass
class ChannelResult:
    """Resultado do processamento de um canal."""

    channel_id: str
    playlist_id: str
    added: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


@dataclass
class RunReport:
    """Resumo de uma execução completa do bot."""

    channels: list[ChannelResult] = field(default_factory=list)
    quota_exceeded: bool = False

    @property
    def total_added(self) -> int:
        return sum(len(c.added) for c in self.channels)

    @property
    def failed(self) -> list[ChannelResult]:
        return [c for c in self.channels if c.error]


# ============================================================
# Regras de negócio
# ============================================================

def is_recent(published_at_iso: str, now: datetime | None = None) -> bool:
    """Retorna True se o vídeo foi publicado dentro da janela configurada."""
    published = datetime.fromisoformat(published_at_iso.replace("Z", "+00:00"))
    now = now or datetime.now(timezone.utc)
    return now - published <= timedelta(days=config.MAX_VIDEO_AGE_DAYS)


def _published_at(item: dict[str, Any]) -> str:
    """
    Data de publicação do vídeo.

    Prefere contentDetails.videoPublishedAt (data real do vídeo); o
    snippet.publishedAt é a data em que o item entrou na playlist.
    """
    return item.get("contentDetails", {}).get("videoPublishedAt") or item["snippet"][
        "publishedAt"
    ]


def process_channel(
    youtube: Any,
    channel_id: str,
    playlist_id: str,
    state: dict[str, Any],
    liked_videos: set[str],
    existing_playlist_videos: set[str],
) -> ChannelResult:
    """
    Processa um canal: busca uploads recentes e adiciona os elegíveis à playlist.

    Critérios de elegibilidade (todos devem ser verdadeiros):
    - Não foi processado anteriormente (state)
    - Não está em nenhuma playlist monitorada
    - Foi publicado há menos de MAX_VIDEO_AGE_DAYS dias
    - Não foi curtido pelo usuário autenticado

    Limita inserções a MAX_VIDEOS_PER_CHANNEL por execução (proteção de quota).

    Raises
    ------
    QuotaExceededError
        Propagada para que o orquestrador interrompa TODOS os canais.
    """
    result = ChannelResult(channel_id=channel_id, playlist_id=playlist_id)
    items = list_latest_uploads(youtube, get_uploads_playlist_id(channel_id))

    for item in items:
        video_id: str = item["snippet"]["resourceId"]["videoId"]

        if is_processed(state, channel_id, video_id):
            continue

        skip_reason = None
        if video_id in existing_playlist_videos:
            skip_reason = "já em playlist"
        elif not is_recent(_published_at(item)):
            skip_reason = "antigo"
        elif video_id in liked_videos:
            skip_reason = "curtido"

        if skip_reason:
            logger.info("[%s] Ignorado (%s): %s", channel_id, skip_reason, video_id)
            result.skip(skip_reason)
            mark_processed(state, channel_id, video_id)
            continue

        add_video_to_playlist(youtube, playlist_id, video_id)
        logger.info("[%s] Adicionado em %s: %s", channel_id, playlist_id, video_id)
        mark_processed(state, channel_id, video_id)
        existing_playlist_videos.add(video_id)
        result.added.append(video_id)

        if len(result.added) >= config.MAX_VIDEOS_PER_CHANNEL:
            break

    logger.info("[%s] %d vídeo(s) adicionado(s)", channel_id, len(result.added))
    return result


# ============================================================
# Orquestração
# ============================================================

def run(youtube: Any, channel_playlist_map: dict[str, str], state: dict[str, Any]) -> RunReport:
    """
    Executa o bot sobre todos os canais.

    Um erro em um canal (canal removido, playlist sem permissão, 5xx após
    retries) é registrado e NÃO impede os demais. Falta de quota interrompe
    a execução inteira, pois todas as chamadas seguintes falhariam também.
    """
    report = RunReport()

    try:
        liked_videos = get_liked_videos(youtube, state)
        existing = get_all_playlist_video_ids(youtube, set(channel_playlist_map.values()))
    except QuotaExceededError:
        logger.warning("Quota da API esgotada antes de processar os canais.")
        report.quota_exceeded = True
        return report
    logger.info("Vídeos já presentes nas playlists: %d", len(existing))

    for channel_id, playlist_id in channel_playlist_map.items():
        logger.info("Processando canal: %s → playlist: %s", channel_id, playlist_id)
        try:
            report.channels.append(
                process_channel(youtube, channel_id, playlist_id, state, liked_videos, existing)
            )
        except QuotaExceededError:
            logger.warning("Quota da API esgotada — interrompendo a execução.")
            report.quota_exceeded = True
            break
        except Exception as exc:  # noqa: BLE001 — isolamento por canal é intencional
            logger.exception("[%s] Falha ao processar canal", channel_id)
            report.channels.append(
                ChannelResult(channel_id, playlist_id, error=f"{type(exc).__name__}: {exc}")
            )

    return report


def _record_run(state: dict[str, Any], report: RunReport) -> None:
    """Guarda um resumo da última execução no state (útil para depuração)."""
    state["_last_run"] = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "added": report.total_added,
        "failed_channels": [c.channel_id for c in report.failed],
        "quota_exceeded": report.quota_exceeded,
    }


def write_github_summary(report: RunReport) -> None:
    """Escreve um resumo em Markdown na página da execução do GitHub Actions."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    lines = [
        "## youtube-playlist-bot",
        "",
        f"- Vídeos adicionados: **{report.total_added}**",
        f"- Canais com falha: **{len(report.failed)}**",
        f"- Quota esgotada: **{'sim' if report.quota_exceeded else 'não'}**",
        "",
        "| Canal | Adicionados | Ignorados | Erro |",
        "|---|---|---|---|",
    ]
    for c in report.channels:
        skipped = ", ".join(f"{k}: {v}" for k, v in c.skipped.items()) or "-"
        lines.append(f"| `{c.channel_id}` | {len(c.added)} | {skipped} | {c.error or '-'} |")

    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    """
    Entrypoint principal do bot. Retorna o exit code do processo:
    0 = sucesso (inclusive quota esgotada, que é esperado), 1 = algum canal falhou.
    """
    logger.info("Iniciando youtube-playlist-bot")

    channel_playlist_map = config.load_channel_playlist_map()
    logger.info("Canais configurados: %d", len(channel_playlist_map))

    state = load_state()
    report = RunReport()
    try:
        creds = load_creds(state)
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        report = run(youtube, channel_playlist_map, state)
    finally:
        _record_run(state, report)
        save_state(state)
        logger.info("State salvo.")

    write_github_summary(report)
    logger.info(
        "Execução concluída: %d adicionado(s), %d canal(is) com falha%s.",
        report.total_added,
        len(report.failed),
        ", quota esgotada" if report.quota_exceeded else "",
    )
    return 1 if report.failed else 0
