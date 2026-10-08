"""History persistence and recovery: nothing that was saved is ever lost."""
import importlib
import json
import os
import sqlite3
import time

import pytest

from fake_engines import text_wav

hs = importlib.import_module("services.history_service")
hdb = importlib.import_module("services.history_db")


TRASHED: list[str] = []


@pytest.fixture
def hdir(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "_MUSIC_DIR", tmp_path)
    TRASHED.clear()
    monkeypatch.setattr(hs, "_trash", lambda path: (TRASHED.append(path.name), path.unlink()))
    return hs.ensure_history_dir()


def _save(text, backend="espeak-ng", audio=True, tmp=None):
    path = None
    if audio:
        path = text_wav(tmp / f"{text}.wav", text)
    hs.save_history_entry(text=text, audio_path=path, backend=backend, voice_id="v")


def test_survives_a_restart(hdir, tmp_path):
    _save("um", tmp=tmp_path)
    # A new process opens the same files: reload the modules from scratch.
    importlib.reload(hdb)
    rows = hs.load_history_entries()
    assert [r["text"] for r in rows] == ["um"]
    assert hs.audio_path(rows[0]) is not None


def test_orphan_files_become_entries_again(hdir, tmp_path):
    # Files without an index row: an older version, a lost index, or files
    # restored from the Trash.
    text_wav(hdir / "2026-04-20_17-30-12_piper.wav", "x")
    (hdir / "2026-04-20_17-30-12_piper.txt").write_text("Texto antigo", encoding="utf-8")
    (hdir / "2026-05-01_08-00-00-123456_kokoro.txt").write_text("Só texto", encoding="utf-8")
    report = hs.repair_history()
    assert report["recovered"] == 2
    rows = {r["text"]: r for r in hs.load_history_entries()}
    assert rows["Texto antigo"]["has_audio"] and rows["Texto antigo"]["duration"] > 0
    assert not rows["Só texto"]["has_audio"]
    assert hs.repair_history()["recovered"] == 0  # no duplicates on the next start


def test_damaged_index_is_kept_aside_and_rebuilt(hdir, tmp_path):
    _save("um", tmp=tmp_path)
    _save("dois", tmp=tmp_path)
    db = hdir / "history.db"
    for suffix in ("-wal", "-shm"):
        (hdir / f"history.db{suffix}").unlink(missing_ok=True)
    db.write_bytes(b"SQLite format 3\x00" + os.urandom(4096))
    report = hs.repair_history()
    assert report["rebuilt"] and report["recovered"] == 2
    assert sorted(r["text"] for r in hs.load_history_entries()) == ["dois", "um"]
    assert list(hdir.glob("history.db.damaged-*"))  # the original is preserved


def test_recording_interrupted_by_a_crash_is_recovered(hdir, tmp_path):
    part = hdir / ".rec-2026-10-08_10-00-00-000001_kokoro.wav.part"
    text_wav(part, "audio")
    old = time.time() - 3600
    os.utime(part, (old, old))
    hs.repair_history()
    [row] = hs.load_history_entries()
    assert row["backend"] == "kokoro" and row["has_audio"]
    assert not part.exists()


def test_a_recording_in_progress_is_left_alone(hdir):
    part = hdir / ".rec-2026-10-08_10-00-00-000001_kokoro.wav.part"
    text_wav(part, "audio")
    hs.repair_history()
    assert part.exists() and hs.load_history_entries() == []


def test_missing_audio_keeps_the_entry_as_text(hdir, tmp_path):
    _save("um", tmp=tmp_path)
    [row] = hs.load_history_entries()
    hs.audio_path(row).unlink()
    report = hs.repair_history()
    assert report["audio_missing"] == 1
    [row] = hs.load_history_entries()
    assert row["text"] == "um" and not row["has_audio"]


def test_legacy_json_is_migrated_once(hdir):
    entries = [{"timestamp": "2026-04-20_17-30-12", "backend": "piper", "voice_id": "v",
                "text_preview": "antigo", "has_audio": False}]
    (hdir / "history.json").write_text(json.dumps(entries), encoding="utf-8")
    assert hs.repair_history()["migrated"] == 1
    # Even if the JSON could not be renamed, a second run adds nothing.
    (hdir / "history.json").write_text(json.dumps(entries), encoding="utf-8")
    hs.repair_history()
    assert hs.count_history_entries() == 1


def test_old_databases_are_upgraded_in_place(hdir):
    db = hdir / "history.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE entries (id TEXT PRIMARY KEY, ts TEXT NOT NULL, backend TEXT, voice_id TEXT,"
        " text TEXT, text_preview TEXT, has_audio INTEGER DEFAULT 0, created_at REAL);"
        "INSERT INTO entries VALUES ('a', '2026-09-15_10-00-00-000000', 'piper', 'v', 't', 't', 0, 1);"
    )
    conn.commit()
    conn.close()
    [row] = hs.load_history_entries()
    assert row["text"] == "t" and row["status"] == "completed" and row["duration"] == 0


def test_delete_moves_files_to_the_trash_and_removes_the_entry(hdir, tmp_path):
    _save("um", tmp=tmp_path)
    _save("dois", tmp=tmp_path)
    one = [r for r in hs.load_history_entries() if r["text"] == "um"]
    assert hs.delete_entries([one[0]["id"]]) == 1
    assert [r["text"] for r in hs.load_history_entries()] == ["dois"]
    assert sorted(n.rsplit(".", 1)[1] for n in TRASHED) == ["txt", "wav"]
    assert hs.repair_history()["recovered"] == 0  # not resurrected


def test_filters_search_order_and_paging(hdir):
    now = time.time()
    for i, (backend, age_days) in enumerate([("piper", 0), ("kokoro", 1), ("rhvoice", 3), ("piper", 30)]):
        ts = hs.new_timestamp(now - age_days * 86400 - i)
        hdb.insert_entry(ts=ts, backend=backend, voice_id=f"voz{i}", text=f"texto número {i}",
                         text_preview=f"texto número {i}", has_audio=False)
    assert hs.count_history_entries(backend="piper") == 2
    assert hs.count_history_entries(since=now - 2 * 86400) == 2
    assert [r["text"] for r in hs.load_history_entries(query="NÚMERO 2")] == ["texto número 2"]
    assert hs.count_history_entries(query="voz3") == 1  # by voice
    assert [r["backend"] for r in hs.load_history_entries(newest_first=False)][0] == "piper"
    assert len(hs.load_history_entries(limit=2, offset=2)) == 2


def test_save_never_overwrites_an_existing_entry(hdir, tmp_path):
    started = time.time()
    for _ in range(3):
        hs.save_reading(text="mesmo instante", backend="rhvoice", voice_id="v", started=started)
    assert len(list(hdir.glob("*.txt"))) == 3
    assert hs.count_history_entries() == 3


def test_unwritable_folder_is_reported_not_raised(tmp_path, monkeypatch):
    # The Music folder is read-only: the history folder cannot be created.
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setattr(hs, "_MUSIC_DIR", locked)
    try:
        assert hs.save_reading(text="x", backend="rhvoice", voice_id="v") is None
        assert hs.new_recording("rhvoice", time.time()) is None
        assert hs.repair_history()["error"] == ""  # nothing to repair, no crash
    finally:
        locked.chmod(0o700)


def test_durations_of_older_entries_are_filled_in(hdir, tmp_path):
    text_wav(hdir / "2026-09-15_10-00-00-000000_piper.wav", "audio")
    hdb.insert_entry(ts="2026-09-15_10-00-00-000000", backend="piper", voice_id="v",
                     text="t", text_preview="t", has_audio=True)
    hs.repair_history()
    [row] = hs.load_history_entries()
    assert row["duration"] > 0
