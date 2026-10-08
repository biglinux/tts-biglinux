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


def test_newest_entry_comes_first(tmp_path, monkeypatch):
    hs = importlib.import_module("services.history_service")
    monkeypatch.setattr(hs, "_MUSIC_DIR", tmp_path)
    for i in range(3):
        hs.save_history_entry(text=f"t{i}", audio_path=None, backend="rhvoice", voice_id="v")
    assert [e["text_preview"] for e in hs.load_history_entries()] == ["t2", "t1", "t0"]


def test_text_is_not_kept_when_save_text_is_off(tmp_path, monkeypatch):
    hs = importlib.import_module("services.history_service")
    monkeypatch.setattr(hs, "_MUSIC_DIR", tmp_path)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF" + b"\0" * 200)
    hs.save_history_entry(text="segredo", audio_path=str(wav), backend="espeak-ng",
                          voice_id="v", save_audio=True, save_text=False)
    hs.save_history_entry(text="nada a guardar", audio_path=None, backend="rhvoice",
                          voice_id="v", save_audio=True, save_text=False)
    entries = hs.load_history_entries()
    assert len(entries) == 1 and entries[0]["text_preview"] == "" and entries[0]["has_audio"]
    assert not list((tmp_path / "tts-biglinux").glob("*.txt"))


def test_history_folder_is_private(tmp_path, monkeypatch):
    hs = importlib.import_module("services.history_service")
    monkeypatch.setattr(hs, "_MUSIC_DIR", tmp_path)
    assert (hs.ensure_history_dir().stat().st_mode & 0o777) == 0o700
