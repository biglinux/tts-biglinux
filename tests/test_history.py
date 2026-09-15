"""History service tests — the timestamp-collision / data-loss regression."""
import importlib


def test_rapid_saves_do_not_collide(tmp_path, monkeypatch):
    hs = importlib.import_module("services.history_service")
    monkeypatch.setattr(hs, "_MUSIC_DIR", tmp_path)

    # Two entries in the same second, same backend — must NOT overwrite.
    for i in range(5):
        hs.save_history_entry(
            text=f"linha {i}",
            audio_path=None,
            backend="piper",
            voice_id="pt_BR-faber-medium",
            save_audio=False,
            save_text=True,
        )

    history_dir = tmp_path / "tts-biglinux"
    import json
    entries = json.loads((history_dir / "history.json").read_text())
    assert len(entries) == 5, "all 5 entries must be preserved"

    ids = [e["id"] for e in entries]
    assert len(set(ids)) == 5, "every entry must have a unique id"

    # Every text file must be distinct (no overwrite)
    txt_files = list(history_dir.glob("*.txt"))
    assert len(txt_files) == 5, f"expected 5 distinct .txt files, got {len(txt_files)}"
