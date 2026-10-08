"""History service tests — collision/data-loss regression (now SQLite-backed)."""
import importlib


def test_rapid_saves_do_not_collide(tmp_path, monkeypatch):
    hs = importlib.import_module("services.history_service")
    monkeypatch.setattr(hs, "_MUSIC_DIR", tmp_path)

    for i in range(5):
        hs.save_history_entry(
            text=f"linha {i}", audio_path=None, backend="piper",
            voice_id="pt_BR-faber-medium", save_audio=False, save_text=True,
        )

    history_dir = tmp_path / "tts-biglinux"
    # All 5 preserved in the DB with unique ids
    entries = hs.load_history_entries()
    assert len(entries) == 5
    assert len({e["id"] for e in entries}) == 5
    # 5 distinct text files on disk (no overwrite)
    assert len(list(history_dir.glob("*.txt"))) == 5


def test_retention_on_save(tmp_path, monkeypatch):
    hs = importlib.import_module("services.history_service")
    monkeypatch.setattr(hs, "_MUSIC_DIR", tmp_path)
    for i in range(10):
        hs.save_history_entry(
            text=f"n{i}", audio_path=None, backend="piper", voice_id="v",
            save_audio=False, save_text=True, max_entries=3,
        )
    assert hs.count_history_entries() == 3
