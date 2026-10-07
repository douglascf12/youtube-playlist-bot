"""
tests/test_auth.py
==================
Testes de carregamento/renovação das credenciais OAuth2.
"""

import json
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError

from youtube_bot import auth


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", json.dumps({"refresh_token": "r"}))
    mock = MagicMock()
    monkeypatch.setattr(
        auth.Credentials, "from_authorized_user_info", lambda info, scopes: mock
    )
    return mock


def test_valid_creds_are_returned_without_refresh(creds):
    creds.valid = True
    assert auth.load_creds({}) is creds
    creds.refresh.assert_not_called()


def test_expired_creds_are_refreshed_and_recorded(creds):
    creds.valid = False
    creds.refresh_token = "r"
    state: dict = {}
    auth.load_creds(state)
    creds.refresh.assert_called_once()
    assert "_last_token_refresh" in state


def test_revoked_refresh_token_gives_actionable_error(creds):
    creds.valid = False
    creds.refresh_token = "r"
    creds.refresh.side_effect = RefreshError("invalid_grant")
    with pytest.raises(RuntimeError, match="bootstrap.py"):
        auth.load_creds({})


def test_missing_refresh_token_raises(creds):
    creds.valid = False
    creds.refresh_token = None
    with pytest.raises(RuntimeError):
        auth.load_creds({})


def test_invalid_json_does_not_leak_secret(monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", '{"token": "SEGREDO"')
    with pytest.raises(RuntimeError) as exc:
        auth.load_creds({})
    assert "SEGREDO" not in str(exc.value)
