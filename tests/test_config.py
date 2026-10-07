"""
tests/test_config.py
====================
Testes de leitura/validação de configuração via variáveis de ambiente.
"""

import json

import pytest

from youtube_bot.config import _int_env, get_required_env, load_channel_playlist_map

CH = "UC" + "a" * 22
PL = "PL" + "b" * 32


class TestGetRequiredEnv:
    def test_returns_value(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        assert get_required_env("FOO") == "bar"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_raises_when_missing_or_blank(self, monkeypatch, value):
        if value is None:
            monkeypatch.delenv("FOO", raising=False)
        else:
            monkeypatch.setenv("FOO", value)
        with pytest.raises(EnvironmentError):
            get_required_env("FOO")


class TestIntEnv:
    def test_default_when_missing(self, monkeypatch):
        monkeypatch.delenv("N", raising=False)
        assert _int_env("N", 7) == 7

    def test_default_when_blank(self, monkeypatch):
        """GitHub passa string vazia quando a Variable não está definida."""
        monkeypatch.setenv("N", "")
        assert _int_env("N", 7) == 7

    def test_reads_value(self, monkeypatch):
        monkeypatch.setenv("N", "3")
        assert _int_env("N", 7) == 3

    @pytest.mark.parametrize("value", ["abc", "0", "-1"])
    def test_rejects_invalid(self, monkeypatch, value):
        monkeypatch.setenv("N", value)
        with pytest.raises(ValueError):
            _int_env("N", 7)


class TestLoadChannelPlaylistMap:
    def test_valid_map(self, monkeypatch):
        monkeypatch.setenv("YT_CHANNEL_PLAYLIST_MAP", json.dumps({CH: PL}))
        assert load_channel_playlist_map() == {CH: PL}

    def test_invalid_json_does_not_leak_content(self, monkeypatch):
        monkeypatch.setenv("YT_CHANNEL_PLAYLIST_MAP", '{"segredo": ')
        with pytest.raises(ValueError) as exc:
            load_channel_playlist_map()
        assert "segredo" not in str(exc.value)

    @pytest.mark.parametrize("payload", ["{}", "[]", '"texto"'])
    def test_rejects_empty_or_non_object(self, monkeypatch, payload):
        monkeypatch.setenv("YT_CHANNEL_PLAYLIST_MAP", payload)
        with pytest.raises(ValueError):
            load_channel_playlist_map()

    def test_rejects_invalid_ids(self, monkeypatch):
        monkeypatch.setenv(
            "YT_CHANNEL_PLAYLIST_MAP", json.dumps({"@meucanal": PL, CH: "x"})
        )
        with pytest.raises(ValueError) as exc:
            load_channel_playlist_map()
        assert "channel_id inválido" in str(exc.value)
        assert "playlist_id inválido" in str(exc.value)
