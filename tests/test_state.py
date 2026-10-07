"""
tests/test_state.py
===================
Testes unitários para youtube_bot.state.

Cobertura:
  - load_state: arquivo ausente, arquivo válido, ordem preservada, arquivo corrompido
  - save_state: persistência, conversão set→list, round-trip, escrita atômica
  - is_processed: vídeo presente, ausente, canal ausente
  - mark_processed: marcação, idempotência, corte FIFO a 300
"""

import json

from youtube_bot.state import (
    is_processed,
    load_state,
    mark_processed,
    save_state,
)

# ---------------------------------------------------------------------------
# load_state
# ---------------------------------------------------------------------------

class TestLoadState:
    def test_returns_empty_dict_when_file_missing(self, tmp_path, monkeypatch):
        """Sem arquivo de state, retorna dict vazio sem erros."""
        monkeypatch.chdir(tmp_path)
        result = load_state()
        assert result == {}

    def test_loads_existing_state_file(self, tmp_path, monkeypatch):
        """Carrega corretamente um state.json existente."""
        monkeypatch.chdir(tmp_path)
        data = {
            "UCchannel1": {"processed": ["vid_001", "vid_002"]},
            "_last_token_refresh": "2026-06-09T10:00:00+00:00",
        }
        (tmp_path / "state.json").write_text(json.dumps(data), encoding="utf-8")

        result = load_state()

        assert "_last_token_refresh" in result
        assert "UCchannel1" in result

    def test_preserves_processed_order(self, tmp_path, monkeypatch):
        """A lista 'processed' é mantida em ordem (necessário para o corte FIFO)."""
        monkeypatch.chdir(tmp_path)
        data = {"UCchannel1": {"processed": ["vid_002", "vid_001"]}}
        (tmp_path / "state.json").write_text(json.dumps(data), encoding="utf-8")

        result = load_state()

        assert result["UCchannel1"]["processed"] == ["vid_002", "vid_001"]

    def test_corrupted_file_returns_empty_state(self, tmp_path, monkeypatch):
        """Um state.json corrompido não derruba o bot."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "state.json").write_text("{not json", encoding="utf-8")

        assert load_state() == {}

    def test_does_not_convert_non_channel_keys(self, tmp_path, monkeypatch):
        """Chaves internas (_liked_videos_cache, etc.) não são tocadas."""
        monkeypatch.chdir(tmp_path)
        data = {
            "_liked_videos_cache": {"ids": ["v1", "v2"], "cached_at": "2026-06-09T10:00:00Z"}
        }
        (tmp_path / "state.json").write_text(json.dumps(data), encoding="utf-8")

        result = load_state()

        # Deve ser dict, não set
        assert isinstance(result["_liked_videos_cache"], dict)
        assert result["_liked_videos_cache"]["ids"] == ["v1", "v2"]


# ---------------------------------------------------------------------------
# save_state
# ---------------------------------------------------------------------------

class TestSaveState:
    def test_creates_state_file(self, tmp_path, monkeypatch):
        """Cria o arquivo state.json se não existir."""
        monkeypatch.chdir(tmp_path)
        save_state({"UCch": {"processed": set()}})
        assert (tmp_path / "state.json").exists()

    def test_converts_set_to_list_for_json(self, tmp_path, monkeypatch):
        """Sets são convertidos para listas antes de serializar."""
        monkeypatch.chdir(tmp_path)
        state = {"UCchannel1": {"processed": {"vid_001", "vid_002"}}}
        save_state(state)

        raw = json.loads((tmp_path / "state.json").read_text())
        assert isinstance(raw["UCchannel1"]["processed"], list)

    def test_round_trip_preserves_video_ids(self, tmp_path, monkeypatch):
        """load_state(save_state(x)) preserva todos os IDs de vídeo."""
        monkeypatch.chdir(tmp_path)
        original_ids = {"vid_001", "vid_002", "vid_003"}
        state = {"UCchannel1": {"processed": original_ids}}

        save_state(state)
        loaded = load_state()

        assert set(loaded["UCchannel1"]["processed"]) == original_ids

    def test_atomic_write_leaves_no_temp_files(self, tmp_path, monkeypatch):
        """A escrita via arquivo temporário + os.replace não deixa lixo."""
        monkeypatch.chdir(tmp_path)
        save_state({"UCch": {"processed": ["v1"]}})
        assert [p.name for p in tmp_path.iterdir()] == ["state.json"]

    def test_preserves_non_channel_keys(self, tmp_path, monkeypatch):
        """Chaves internas como _liked_videos_cache são salvas sem alteração."""
        monkeypatch.chdir(tmp_path)
        cache = {"ids": ["v1"], "cached_at": "2026-06-09T10:00:00Z"}
        state = {"_liked_videos_cache": cache}

        save_state(state)
        raw = json.loads((tmp_path / "state.json").read_text())

        assert raw["_liked_videos_cache"] == cache


# ---------------------------------------------------------------------------
# is_processed
# ---------------------------------------------------------------------------

class TestIsProcessed:
    def test_returns_false_for_empty_state(self):
        assert is_processed({}, "UCch", "vid_001") is False

    def test_returns_false_for_unknown_channel(self):
        state = {"UCother": {"processed": {"vid_001"}}}
        assert is_processed(state, "UCch", "vid_001") is False

    def test_returns_false_for_unknown_video(self):
        state = {"UCch": {"processed": {"vid_002"}}}
        assert is_processed(state, "UCch", "vid_001") is False

    def test_returns_true_for_known_video(self):
        state = {"UCch": {"processed": {"vid_001"}}}
        assert is_processed(state, "UCch", "vid_001") is True

    def test_works_with_list_as_fallback(self):
        """Compatível com state carregado antes da conversão list→set."""
        state = {"UCch": {"processed": ["vid_001", "vid_002"]}}
        assert is_processed(state, "UCch", "vid_001") is True


# ---------------------------------------------------------------------------
# mark_processed
# ---------------------------------------------------------------------------

class TestMarkProcessed:
    def test_creates_channel_entry_if_missing(self):
        state: dict = {}
        mark_processed(state, "UCch", "vid_001")
        assert "UCch" in state
        assert "vid_001" in state["UCch"]["processed"]

    def test_adds_video_to_existing_channel(self):
        state = {"UCch": {"processed": {"vid_001"}}}
        mark_processed(state, "UCch", "vid_002")
        assert "vid_002" in state["UCch"]["processed"]

    def test_idempotent_double_mark(self):
        """Marcar o mesmo vídeo duas vezes não duplica."""
        state: dict = {}
        mark_processed(state, "UCch", "vid_001")
        mark_processed(state, "UCch", "vid_001")
        assert len(state["UCch"]["processed"]) == 1

    def test_caps_at_300_entries(self):
        """Após 300+ entradas, o tamanho é limitado a 300."""
        state: dict = {}
        for i in range(350):
            mark_processed(state, "UCch", f"vid_{i:04d}")
        assert len(state["UCch"]["processed"]) <= 300

    def test_trimming_discards_oldest_ids(self):
        """O corte remove os IDs mais antigos e mantém os mais recentes."""
        state: dict = {}
        for i in range(305):
            mark_processed(state, "UCch", f"vid_{i:04d}")

        processed = state["UCch"]["processed"]
        assert len(processed) == 300
        assert "vid_0000" not in processed
        assert "vid_0004" not in processed
        assert processed[0] == "vid_0005"
        assert processed[-1] == "vid_0304"

    def test_accepts_legacy_set(self):
        """States antigos com 'processed' como set continuam funcionando."""
        state = {"UCch": {"processed": {"vid_001"}}}
        mark_processed(state, "UCch", "vid_002")
        assert set(state["UCch"]["processed"]) == {"vid_001", "vid_002"}

    def test_does_not_exceed_300_after_cap(self):
        """Adicionar mais vídeos após atingir o limite não ultrapassa 300."""
        state: dict = {}
        for i in range(310):
            mark_processed(state, "UCch", f"vid_{i:04d}")

        # Adicionar mais 10
        for i in range(310, 320):
            mark_processed(state, "UCch", f"vid_{i:04d}")

        assert len(state["UCch"]["processed"]) <= 300
