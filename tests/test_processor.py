"""
tests/test_processor.py
========================
Testes unitários para youtube_bot.processor.

Cobertura:
  - is_recent: vídeos dentro e fora da janela de dias
  - process_channel: todos os critérios de filtragem
    - vídeo já processado → skip silencioso
    - vídeo já em playlist → ignorado + marcado
    - vídeo antigo        → ignorado + marcado
    - vídeo curtido       → ignorado + marcado
    - vídeo elegível      → inserido + marcado + added++
    - limite MAX_VIDEOS_PER_CHANNEL → para após N inserções
    - erro HTTP 403       → salva state e faz return
    - erro HTTP 500       → propaga exceção
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest
from googleapiclient.errors import HttpError

from youtube_bot.processor import is_recent, process_channel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_iso(days_ago: int) -> str:
    """Retorna ISO 8601 UTC de N dias atrás."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_playlist_item(video_id: str, days_ago: int) -> dict:
    """Cria um item de playlistItems.list() com o formato real da API."""
    return {
        "snippet": {
            "resourceId": {"videoId": video_id},
            "publishedAt": make_iso(days_ago),
        }
    }


def make_http_error(status: int) -> HttpError:
    """Cria um HttpError com o status code desejado."""
    resp = MagicMock()
    resp.status = status
    return HttpError(resp=resp, content=b"error")


# ---------------------------------------------------------------------------
# is_recent
# ---------------------------------------------------------------------------

class TestIsRecent:
    def test_video_published_today_is_recent(self):
        assert is_recent(make_iso(0)) is True

    def test_video_published_within_window_is_recent(self):
        assert is_recent(make_iso(100)) is True

    def test_video_published_at_boundary_is_recent(self):
        """Exatamente no limite (149 dias) deve ser recente."""
        assert is_recent(make_iso(149)) is True

    def test_video_published_beyond_window_is_not_recent(self):
        assert is_recent(make_iso(151)) is False

    def test_video_published_very_old_is_not_recent(self):
        assert is_recent(make_iso(365)) is False

    def test_handles_z_suffix_in_iso_string(self):
        """A API do YouTube usa 'Z' no final — deve ser tratado corretamente."""
        iso = "2020-01-01T00:00:00Z"
        assert is_recent(iso) is False


# ---------------------------------------------------------------------------
# process_channel — setup
# ---------------------------------------------------------------------------

CHANNEL = "UCtest_channel"
PLAYLIST = "PLtest_playlist"
UPLOADS_PLAYLIST = "UUtest_uploads"


@pytest.fixture
def youtube_mock():
    """Mock do cliente YouTube com uploads_playlist configurado."""
    mock = MagicMock()
    # get_uploads_playlist_id
    mock.channels().list().execute.return_value = {
        "items": [{
            "contentDetails": {
                "relatedPlaylists": {"uploads": UPLOADS_PLAYLIST}
            }
        }]
    }
    return mock


# ---------------------------------------------------------------------------
# process_channel — testes
# ---------------------------------------------------------------------------

class TestProcessChannel:
    def _run(
        self,
        youtube_mock,
        items: list[dict],
        state: dict | None = None,
        liked: set[str] | None = None,
        existing: set[str] | None = None,
    ) -> dict:
        """Helper: configura uploads e executa process_channel."""
        youtube_mock.playlistItems().list().execute.return_value = {"items": items}
        s = state if state is not None else {}
        process_channel(
            youtube=youtube_mock,
            channel_id=CHANNEL,
            playlist_id=PLAYLIST,
            state=s,
            liked_videos=liked or set(),
            existing_playlist_videos=existing or set(),
        )
        return s

    # --- vídeo já processado ---

    def test_skips_already_processed_video(self, youtube_mock):
        """Vídeo no state não deve chamar add_video_to_playlist."""
        state = {CHANNEL: {"processed": {"vid_001"}}}
        items = [make_playlist_item("vid_001", 10)]

        self._run(youtube_mock, items, state=state)

        youtube_mock.playlistItems().insert.assert_not_called()

    # --- vídeo já em playlist ---

    def test_skips_video_already_in_playlist(self, youtube_mock):
        """Vídeo já em playlist é ignorado e marcado como processado."""
        items = [make_playlist_item("vid_001", 10)]
        state = self._run(youtube_mock, items, existing={"vid_001"})

        youtube_mock.playlistItems().insert.assert_not_called()
        assert "vid_001" in state[CHANNEL]["processed"]

    # --- vídeo antigo ---

    def test_skips_old_video(self, youtube_mock):
        """Vídeo publicado há mais de MAX_VIDEO_AGE_DAYS dias é ignorado."""
        items = [make_playlist_item("vid_old", 200)]
        state = self._run(youtube_mock, items)

        youtube_mock.playlistItems().insert.assert_not_called()
        assert "vid_old" in state[CHANNEL]["processed"]

    # --- vídeo curtido ---

    def test_skips_liked_video(self, youtube_mock):
        """Vídeo curtido pelo usuário é ignorado."""
        items = [make_playlist_item("vid_liked", 10)]
        state = self._run(youtube_mock, items, liked={"vid_liked"})

        youtube_mock.playlistItems().insert.assert_not_called()
        assert "vid_liked" in state[CHANNEL]["processed"]

    # --- vídeo elegível ---

    def test_adds_eligible_video_to_playlist(self, youtube_mock):
        """Vídeo elegível é inserido na playlist."""
        items = [make_playlist_item("vid_new", 10)]
        state = self._run(youtube_mock, items)

        youtube_mock.playlistItems().insert.assert_called_once()
        assert "vid_new" in state[CHANNEL]["processed"]

    def test_eligible_video_added_to_existing_set(self, youtube_mock):
        """Após inserção, o video_id é adicionado ao set de existing para dedup."""
        existing: set[str] = set()
        items = [make_playlist_item("vid_new", 10)]
        self._run(youtube_mock, items, existing=existing)

        assert "vid_new" in existing

    # --- limite MAX_VIDEOS_PER_CHANNEL ---

    @patch("youtube_bot.processor.MAX_VIDEOS_PER_CHANNEL", 2)
    def test_respects_max_videos_per_channel(self, youtube_mock):
        """Insere no máximo MAX_VIDEOS_PER_CHANNEL vídeos por execução."""
        items = [
            make_playlist_item("vid_a", 5),
            make_playlist_item("vid_b", 6),
            make_playlist_item("vid_c", 7),
        ]
        self._run(youtube_mock, items)

        # insert deve ter sido chamado exatamente 2 vezes
        assert youtube_mock.playlistItems().insert.call_count == 2

    # --- tratamento de erros ---

    def test_returns_on_403_quota_exceeded(self, youtube_mock):
        """Em erro 403, salva state e retorna sem propagar exceção."""
        youtube_mock.playlistItems().insert().execute.side_effect = make_http_error(403)
        items = [make_playlist_item("vid_new", 10)]

        # Não deve lançar exceção
        with patch("youtube_bot.processor.save_state") as mock_save:
            self._run(youtube_mock, items)
            mock_save.assert_called_once()

    def test_raises_on_non_403_http_error(self, youtube_mock):
        """Erros HTTP diferentes de 403 devem ser propagados."""
        youtube_mock.playlistItems().insert().execute.side_effect = make_http_error(500)
        items = [make_playlist_item("vid_new", 10)]

        with pytest.raises(HttpError):
            self._run(youtube_mock, items)

    # --- canal sem vídeos ---

    def test_handles_empty_uploads(self, youtube_mock):
        """Canal sem uploads recentes não deve causar erro."""
        state = self._run(youtube_mock, items=[])
        youtube_mock.playlistItems().insert.assert_not_called()

    # --- múltiplos vídeos mistos ---

    @patch("youtube_bot.processor.MAX_VIDEOS_PER_CHANNEL", 5)
    def test_mixed_videos_only_eligible_are_inserted(self, youtube_mock):
        """Em uma lista mista, apenas os elegíveis são inseridos."""
        items = [
            make_playlist_item("vid_liked", 10),    # curtido → ignorar
            make_playlist_item("vid_old", 200),     # antigo → ignorar
            make_playlist_item("vid_eligible", 5),  # elegível → inserir
        ]
        state = self._run(youtube_mock, items, liked={"vid_liked"})

        assert youtube_mock.playlistItems().insert.call_count == 1
        assert "vid_eligible" in state[CHANNEL]["processed"]
        assert "vid_liked" in state[CHANNEL]["processed"]
        assert "vid_old" in state[CHANNEL]["processed"]
