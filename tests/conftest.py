"""
conftest.py — fixtures compartilhadas entre todos os testes.
"""

import pytest


# ---------------------------------------------------------------------------
# Factories de state
# ---------------------------------------------------------------------------

@pytest.fixture
def empty_state() -> dict:
    """State vazio — nenhum canal processado ainda."""
    return {}


@pytest.fixture
def state_with_channel() -> dict:
    """State com um canal já contendo vídeos processados."""
    return {
        "UCchannel1": {
            "processed": {"vid_001", "vid_002", "vid_003"},
        }
    }


# ---------------------------------------------------------------------------
# Constantes de teste
# ---------------------------------------------------------------------------

CHANNEL_ID = "UCchannel_test"
PLAYLIST_ID = "PLtest_playlist"
VIDEO_ID = "dQw4w9WgXcQ"
