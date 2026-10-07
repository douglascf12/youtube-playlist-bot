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
    - 403 de quota        → QuotaExceededError
    - 403 sem quota / 500 → propaga HttpError
  - run: isolamento de falhas por canal e parada total por quota
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from youtube_bot.processor import is_recent, process_channel, run
from youtube_bot.state import is_processed
from youtube_bot.youtube_api import QuotaExceededError

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


def make_http_error(status: int, reason: str = "backendError") -> HttpError:
    """Cria um HttpError com status e `reason` no formato real da API."""
    resp = MagicMock()
    resp.status = status
    content = json.dumps({"error": {"code": status, "errors": [{"reason": reason}]}})
    return HttpError(resp=resp, content=content.encode())


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

CHANNEL = "UC" + "a" * 22
PLAYLIST = "PLtest_playlist"


@pytest.fixture
def youtube_mock():
    """Mock do cliente YouTube (a playlist de uploads é derivada, sem API)."""
    return MagicMock()


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
        # Usar "if ... is not None" em vez de "or set()" para não descartar
        # sets vazios (set() é falsy, então "existing or set()" criaria um
        # set anônimo novo quando existing=set(), quebrando a referência)
        liked_set = liked if liked is not None else set()
        existing_set = existing if existing is not None else set()
        process_channel(
            youtube=youtube_mock,
            channel_id=CHANNEL,
            playlist_id=PLAYLIST,
            state=s,
            liked_videos=liked_set,
            existing_playlist_videos=existing_set,
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

    @patch("youtube_bot.config.MAX_VIDEOS_PER_CHANNEL", 2)
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

    def test_quota_error_propagates_as_quota_exceeded(self, youtube_mock):
        """403 de quota vira QuotaExceededError para o orquestrador parar tudo."""
        youtube_mock.playlistItems().insert().execute.side_effect = make_http_error(
            403, reason="quotaExceeded"
        )
        items = [make_playlist_item("vid_new", 10)]

        with pytest.raises(QuotaExceededError):
            self._run(youtube_mock, items)

    def test_forbidden_403_is_not_treated_as_quota(self, youtube_mock):
        """403 sem motivo de quota (ex.: sem permissão) propaga como HttpError."""
        youtube_mock.playlistItems().insert().execute.side_effect = make_http_error(
            403, reason="forbidden"
        )
        items = [make_playlist_item("vid_new", 10)]

        with pytest.raises(HttpError):
            self._run(youtube_mock, items)

    def test_raises_on_non_403_http_error(self, youtube_mock):
        """Erros HTTP diferentes de 403 devem ser propagados."""
        youtube_mock.playlistItems().insert().execute.side_effect = make_http_error(500)
        items = [make_playlist_item("vid_new", 10)]

        with pytest.raises(HttpError):
            self._run(youtube_mock, items)

    def test_failed_insert_does_not_mark_processed(self, youtube_mock):
        """Se a inserção falhar, o vídeo deve ser tentado de novo na próxima execução."""
        youtube_mock.playlistItems().insert().execute.side_effect = make_http_error(500)
        state: dict = {}
        with pytest.raises(HttpError):
            self._run(youtube_mock, [make_playlist_item("vid_new", 10)], state=state)

        assert not is_processed(state, CHANNEL, "vid_new")

    def test_uses_video_published_at_from_content_details(self, youtube_mock):
        """videoPublishedAt (data real) tem prioridade sobre snippet.publishedAt."""
        item = make_playlist_item("vid_old_reupload", 1)
        item["contentDetails"] = {"videoPublishedAt": make_iso(400)}
        self._run(youtube_mock, [item])

        youtube_mock.playlistItems().insert.assert_not_called()

    def test_does_not_call_channels_list(self, youtube_mock):
        """A playlist de uploads é derivada do channel_id, sem gastar quota."""
        self._run(youtube_mock, [make_playlist_item("vid_new", 10)])
        youtube_mock.channels.assert_not_called()
        _, kwargs = youtube_mock.playlistItems().list.call_args
        assert kwargs["playlistId"] == "UU" + CHANNEL[2:]

    # --- canal sem vídeos ---

    def test_handles_empty_uploads(self, youtube_mock):
        """Canal sem uploads recentes não deve causar erro."""
        self._run(youtube_mock, items=[])
        youtube_mock.playlistItems().insert.assert_not_called()

    # --- múltiplos vídeos mistos ---

    @patch("youtube_bot.config.MAX_VIDEOS_PER_CHANNEL", 5)
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


# ---------------------------------------------------------------------------
# run — orquestração
# ---------------------------------------------------------------------------

CH_A = "UC" + "a" * 22
CH_B = "UC" + "b" * 22


class TestRun:
    def _patch_reads(self, monkeypatch):
        monkeypatch.setattr("youtube_bot.processor.get_liked_videos", lambda yt, st: set())
        monkeypatch.setattr(
            "youtube_bot.processor.get_all_playlist_video_ids", lambda yt, ids: set()
        )

    def test_error_in_one_channel_does_not_stop_others(self, monkeypatch):
        self._patch_reads(monkeypatch)
        calls = []

        def fake_process(youtube, channel_id, *args):
            calls.append(channel_id)
            if channel_id == CH_A:
                raise RuntimeError("boom")
            from youtube_bot.processor import ChannelResult
            return ChannelResult(channel_id, "PL" + "x" * 16, added=["v1"])

        monkeypatch.setattr("youtube_bot.processor.process_channel", fake_process)
        report = run(MagicMock(), {CH_A: "PL1", CH_B: "PL2"}, {})

        assert calls == [CH_A, CH_B]
        assert [c.channel_id for c in report.failed] == [CH_A]
        assert report.total_added == 1

    def test_quota_exceeded_stops_all_channels(self, monkeypatch):
        self._patch_reads(monkeypatch)
        calls = []

        def fake_process(youtube, channel_id, *args):
            calls.append(channel_id)
            raise QuotaExceededError("quota")

        monkeypatch.setattr("youtube_bot.processor.process_channel", fake_process)
        report = run(MagicMock(), {CH_A: "PL1", CH_B: "PL2"}, {})

        assert calls == [CH_A]
        assert report.quota_exceeded is True
        assert report.failed == []

    def test_quota_exceeded_during_initial_reads(self, monkeypatch):
        def raise_quota(*_):
            raise QuotaExceededError("quota")

        monkeypatch.setattr("youtube_bot.processor.get_liked_videos", raise_quota)
        report = run(MagicMock(), {CH_A: "PL1"}, {})

        assert report.quota_exceeded is True
        assert report.channels == []


# ---------------------------------------------------------------------------
# main — entrypoint
# ---------------------------------------------------------------------------

class TestMain:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("YT_CHANNEL_PLAYLIST_MAP", json.dumps({CH_A: "PL" + "x" * 16}))
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        monkeypatch.setattr("youtube_bot.processor.load_creds", lambda state: MagicMock())
        monkeypatch.setattr("youtube_bot.processor.build", lambda *a, **k: MagicMock())
        return tmp_path, summary

    def _fake_run(self, monkeypatch, report_factory):
        monkeypatch.setattr("youtube_bot.processor.run", lambda yt, m, st: report_factory())

    def test_success_returns_zero_and_writes_summary(self, env, monkeypatch):
        from youtube_bot.processor import ChannelResult, RunReport, main

        tmp_path, summary = env
        self._fake_run(
            monkeypatch,
            lambda: RunReport(channels=[ChannelResult(CH_A, "PL", added=["v1"])]),
        )

        assert main() == 0
        assert "Vídeos adicionados: **1**" in summary.read_text()
        state = json.loads((tmp_path / "state.json").read_text())
        assert state["_last_run"]["added"] == 1

    def test_failed_channel_returns_one(self, env, monkeypatch):
        from youtube_bot.processor import ChannelResult, RunReport, main

        self._fake_run(
            monkeypatch, lambda: RunReport(channels=[ChannelResult(CH_A, "PL", error="x")])
        )
        assert main() == 1

    def test_state_saved_even_when_auth_fails(self, env, monkeypatch):
        from youtube_bot.processor import main

        tmp_path, _ = env

        def boom(state):
            raise RuntimeError("auth")

        monkeypatch.setattr("youtube_bot.processor.load_creds", boom)
        with pytest.raises(RuntimeError):
            main()
        assert (tmp_path / "state.json").exists()
