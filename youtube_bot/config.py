"""
youtube_bot.config
==================
Constantes de configuração e utilitários de leitura de variáveis de ambiente.
"""

import os

# Escopo OAuth2 necessário para leitura e escrita em playlists
SCOPES: list[str] = ["https://www.googleapis.com/auth/youtube"]

# Arquivo de state persistido entre execuções
STATE_FILE: str = "state.json"

# Proteção de quota: máximo de vídeos inseridos por canal por execução
MAX_VIDEOS_PER_CHANNEL: int = 2

# Filtro de idade: vídeos mais antigos que isso são ignorados
MAX_VIDEO_AGE_DAYS: int = 150

# TTL do cache de liked videos no state (em horas)
LIKED_CACHE_TTL_HOURS: int = 6


def get_required_env(name: str) -> str:
    """
    Retorna o valor de uma variável de ambiente obrigatória.

    Lança EnvironmentError com mensagem clara se não estiver definida,
    evitando o KeyError genérico do os.environ[].

    Parâmetros
    ----------
    name : str
        Nome da variável de ambiente.

    Raises
    ------
    EnvironmentError
        Se a variável não estiver definida ou estiver vazia.
    """
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(
            f"Variável de ambiente obrigatória não definida: '{name}'.\n"
            "Configure o Secret no GitHub Actions ou em arquivo .env local."
        )
    return value
