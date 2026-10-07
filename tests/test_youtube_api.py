"""
tests/test_youtube_api.py
=========================
Testes da camada de acesso à YouTube Data API (com cliente mockado).
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from youtube_bot.youtube_api import (
    QuotaExceededError,
    add_video_to_playlist,
    get_all_playlist_video_ids,
    get_liked_videos,
    get_uploads_playlist_id,
    is_quota_error,
)


def http_error(status: int, reason: str | None = None) -> HttpError:
    resp = MagicMock()
    resp.status = status
    body = {"error": {"errors": [{"reason": reason}]}} if reason else {}
    return HttpError(resp=resp, content=json.dumps(body).encode())


class TestIsQuotaError:
    @pytest.mark.parametrize("reason", ["quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"])
    def test_quota_reasons(self, reason):
        assert is_quota_error(http_error(403, reason)) is True

    def test_429_is_quota(self):
        assert is_quota_error(http_error(429)) is True

    def test_forbidden_is_not_quota(self):
        assert is_quota_error(http_error(403, "forbidden")) is False

    def test_500_is_not_quota(self):
        assert is_quota_error(http_error(500, "backendError")) is False

    def test_unparseable_body_is_not_quota(self):
        resp = MagicMock()
        resp.status = 403
        assert is_quota_error(HttpError(resp=resp, content=b"<html>")) is False


class TestUploadsPlaylistId:
    def test_derives_uu_prefix(self):
        assert get_uploads_playlist_id("UCabc") == "UUabc"

    def test_rejects_non_channel_id(self):
        with pytest.raises(ValueError):
            get_uploads_playlist_id("@handle")


class TestPagination:
    def test_collects_all_pages(self):
        yt = MagicMock()
        yt.playlistItems().list().execute.side_effect = [
            {"items": [{"contentDetails": {"videoId": "a"}}], "nextPageToken": "p2"},
            {"items": [{"contentDetails": {"videoId": "b"}}]},
        ]
        assert get_all_playlist_video_ids(yt, {"PL1"}) == {"a", "b"}


class TestLikedVideosCache:
    def test_uses_fresh_cache_without_api_call(self):
        yt = MagicMock()
        state = {
            "_liked_videos_cache": {
                "ids": ["v1"],
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }
        }
        assert get_liked_videos(yt, state) == {"v1"}
        yt.videos.assert_not_called()

    def test_refreshes_stale_cache(self):
        yt = MagicMock()
        yt.videos().list().execute.return_value = {"items": [{"id": "v2"}]}
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        state = {"_liked_videos_cache": {"ids": ["v1"], "cached_at": old.isoformat()}}

        assert get_liked_videos(yt, state) == {"v2"}
        assert state["_liked_videos_cache"]["ids"] == ["v2"]


class TestExecute:
    def test_uses_retries(self):
        yt = MagicMock()
        add_video_to_playlist(yt, "PL1", "vid")
        _, kwargs = yt.playlistItems().insert().execute.call_args
        assert kwargs["num_retries"] >= 1

    def test_converts_quota_error(self):
        yt = MagicMock()
        yt.playlistItems().insert().execute.side_effect = http_error(403, "quotaExceeded")
        with pytest.raises(QuotaExceededError):
            add_video_to_playlist(yt, "PL1", "vid")
