"""
youtube_bot.config
==================
Configuração do bot: constantes (sobrescrevíveis por env var) e leitura
validada das variáveis de ambiente obrigatórias.
"""

import json
import os
import re

# Escopo OAuth2 necessário para leitura e escrita em playlists
SCOPES: list[str] = ["https://www.googleapis.com/auth/youtube"]


def _int_env(name: str, default: int) -> int:
    """Lê um inteiro positivo de env var, caindo no default se ausente."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"'{name}' deve ser um inteiro, recebido: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"'{name}' deve ser maior que zero, recebido: {value}")
    return value


# Arquivo de state persistido entre execuções
STATE_FILE: str = os.environ.get("STATE_FILE", "state.json")

# Proteção de quota: máximo de vídeos inseridos por canal por execução
MAX_VIDEOS_PER_CHANNEL: int = _int_env("MAX_VIDEOS_PER_CHANNEL", 2)

# Filtro de idade: vídeos mais antigos que isso são ignorados
MAX_VIDEO_AGE_DAYS: int = _int_env("MAX_VIDEO_AGE_DAYS", 150)

# TTL do cache de liked videos no state (em horas)
LIKED_CACHE_TTL_HOURS: int = _int_env("LIKED_CACHE_TTL_HOURS", 6)

# Quantidade de uploads recentes lidos por canal a cada execução
UPLOADS_LOOKBACK: int = _int_env("UPLOADS_LOOKBACK", 10)

# Máximo de IDs processados guardados por canal no state
MAX_PROCESSED_PER_CHANNEL: int = _int_env("MAX_PROCESSED_PER_CHANNEL", 300)

# Tentativas automáticas do googleapiclient em erros 5xx/429 (backoff exponencial)
API_NUM_RETRIES: int = _int_env("API_NUM_RETRIES", 3)

# IDs de canal do YouTube: "UC" + 22 caracteres base64-url
_CHANNEL_ID_RE = re.compile(r"^UC[\w-]{22}$")
# IDs de playlist: letras, números, "_" e "-"
_PLAYLIST_ID_RE = re.compile(r"^[\w-]{10,}$")


def get_required_env(name: str) -> str:
    """
    Retorna o valor de uma variável de ambiente obrigatória.

    Raises
    ------
    EnvironmentError
        Se a variável não estiver definida ou estiver vazia.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise EnvironmentError(
            f"Variável de ambiente obrigatória não definida: '{name}'. "
            "Configure o Secret no GitHub Actions ou em arquivo .env local."
        )
    return value


def load_channel_playlist_map() -> dict[str, str]:
    """
    Lê e valida YT_CHANNEL_PLAYLIST_MAP (JSON channel_id → playlist_id).

    Falha cedo, com mensagem clara, em vez de deixar um JSON malformado ou um
    ID inválido estourar no meio da execução (gastando quota à toa).

    Raises
    ------
    ValueError
        Se o JSON for inválido, vazio ou contiver IDs com formato inválido.
    """
    raw = get_required_env("YT_CHANNEL_PLAYLIST_MAP")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Não incluir o conteúdo do secret na mensagem
        raise ValueError(
            f"YT_CHANNEL_PLAYLIST_MAP não é um JSON válido (linha {exc.lineno}, "
            f"coluna {exc.colno})."
        ) from None

    if not isinstance(data, dict) or not data:
        raise ValueError("YT_CHANNEL_PLAYLIST_MAP deve ser um objeto JSON não vazio.")

    errors: list[str] = []
    for channel_id, playlist_id in data.items():
        if not isinstance(channel_id, str) or not _CHANNEL_ID_RE.match(channel_id):
            errors.append(f"channel_id inválido: {channel_id!r}")
        if not isinstance(playlist_id, str) or not _PLAYLIST_ID_RE.match(playlist_id):
            errors.append(f"playlist_id inválido para {channel_id!r}: {playlist_id!r}")

    if errors:
        raise ValueError("YT_CHANNEL_PLAYLIST_MAP inválido:\n  - " + "\n  - ".join(errors))

    return data
