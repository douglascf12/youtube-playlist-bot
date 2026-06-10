"""
youtube_bot.state
=================
Gerenciamento do state persistido entre execuções do bot.

O state é um dict JSON salvo em STATE_FILE com a seguinte estrutura:
{
    "<channel_id>": {
        "processed": ["video_id_1", "video_id_2", ...]  // lista em disco, set em memória
    },
    "_liked_videos_cache": {
        "ids": ["video_id_1", ...],
        "cached_at": "2026-06-09T10:00:00Z"
    },
    "_last_token_refresh": "2026-06-09T08:00:00Z"
}
"""

import json
import logging
import os
from typing import Any

from youtube_bot.config import STATE_FILE

logger = logging.getLogger(__name__)


def load_state() -> dict[str, Any]:
    """
    Carrega o state persistido em disco.

    Converte automaticamente as listas de 'processed' para sets em memória,
    reduzindo lookup de O(n) para O(1).

    Returns
    -------
    dict
        State carregado do disco, ou dict vazio se o arquivo não existir.
    """
    if not os.path.exists(STATE_FILE):
        return {}

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)

    # Converter listas → sets para lookup O(1) durante a execução
    for key, value in data.items():
        if isinstance(value, dict) and "processed" in value:
            value["processed"] = set(value["processed"])

    return data


def save_state(state: dict[str, Any]) -> None:
    """
    Persiste o state em disco.

    Converte sets de 'processed' de volta para listas antes de serializar
    (JSON não suporta o tipo set).

    Parâmetros
    ----------
    state : dict
        State atual do bot.
    """
    serializable: dict[str, Any] = {}

    for key, value in state.items():
        if isinstance(value, dict) and "processed" in value:
            serializable[key] = {**value, "processed": list(value["processed"])}
        else:
            serializable[key] = value

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)


def is_processed(state: dict[str, Any], channel_id: str, video_id: str) -> bool:
    """
    Retorna True se o vídeo já foi processado anteriormente neste canal.

    Parâmetros
    ----------
    state : dict
        State atual do bot.
    channel_id : str
        ID do canal do YouTube.
    video_id : str
        ID do vídeo a verificar.
    """
    return video_id in state.get(channel_id, {}).get("processed", set())


def mark_processed(state: dict[str, Any], channel_id: str, video_id: str) -> None:
    """
    Marca um vídeo como processado no state.

    Mantém no máximo os últimos 300 IDs por canal para controlar o tamanho
    do state.json ao longo do tempo.

    Parâmetros
    ----------
    state : dict
        State atual do bot (modificado in-place).
    channel_id : str
        ID do canal do YouTube.
    video_id : str
        ID do vídeo a marcar como processado.
    """
    channel = state.setdefault(channel_id, {})
    processed: set[str] = channel.setdefault("processed", set())
    processed.add(video_id)

    # Controle de tamanho: manter apenas os últimos 300
    if len(processed) > 300:
        channel["processed"] = set(list(processed)[-300:])
