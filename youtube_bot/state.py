"""
youtube_bot.state
=================
Gerenciamento do state persistido entre execuções do bot.

O state é um dict JSON salvo em STATE_FILE com a seguinte estrutura:
{
    "<channel_id>": {
        "processed": ["video_id_1", "video_id_2", ...]  // ordem de inserção
    },
    "_liked_videos_cache": {
        "ids": ["video_id_1", ...],
        "cached_at": "2026-06-09T10:00:00+00:00"
    },
    "_last_token_refresh": "2026-06-09T08:00:00+00:00",
    "_last_run": {...}
}

`processed` é mantido como lista (e não set) para preservar a ordem de
inserção: assim o corte em MAX_PROCESSED_PER_CHANNEL descarta sempre os
IDs MAIS ANTIGOS. Com no máximo algumas centenas de itens, o lookup O(n)
é irrelevante perto do custo de uma chamada HTTP.
"""

import json
import logging
import os
import tempfile
from typing import Any

from youtube_bot import config

logger = logging.getLogger(__name__)


def load_state(path: str | None = None) -> dict[str, Any]:
    """
    Carrega o state persistido em disco.

    Um arquivo corrompido não derruba o bot: é logado e o bot recomeça com
    state vazio (a deduplicação pelas playlists evita inserções repetidas).
    """
    path = path or config.STATE_FILE
    if not os.path.exists(path):
        logger.info("State não encontrado (%s) — iniciando do zero.", path)
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("State ilegível (%s): %s — iniciando do zero.", path, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning("State com formato inesperado — iniciando do zero.")
        return {}

    return data


def save_state(state: dict[str, Any], path: str | None = None) -> None:
    """
    Persiste o state em disco de forma atômica.

    Escreve em um arquivo temporário e faz os.replace(), garantindo que uma
    interrupção no meio da escrita nunca deixe um state.json truncado.
    """
    path = path or config.STATE_FILE
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=_json_default)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _json_default(value: Any) -> Any:
    """Compatibilidade: serializa sets (states antigos) como listas."""
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Tipo não serializável no state: {type(value).__name__}")


def is_processed(state: dict[str, Any], channel_id: str, video_id: str) -> bool:
    """Retorna True se o vídeo já foi processado anteriormente neste canal."""
    return video_id in state.get(channel_id, {}).get("processed", ())


def mark_processed(state: dict[str, Any], channel_id: str, video_id: str) -> None:
    """
    Marca um vídeo como processado no state (idempotente).

    Mantém no máximo os MAX_PROCESSED_PER_CHANNEL IDs mais recentes por canal.
    """
    channel = state.setdefault(channel_id, {})
    processed = channel.get("processed", [])
    if not isinstance(processed, list):  # state antigo carregado como set
        processed = list(processed)

    if video_id in processed:
        channel["processed"] = processed
        return

    processed.append(video_id)
    channel["processed"] = processed[-config.MAX_PROCESSED_PER_CHANNEL:]
