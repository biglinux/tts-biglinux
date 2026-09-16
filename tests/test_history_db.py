"""SQLite history store: insert, list, search, delete, retention, migration."""
import importlib
import json
import time

import pytest

hs = importlib.import_module("services.history_service")
hdb = importlib.import_module("services.history_db")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "_MUSIC_DIR", tmp_path)
    (tmp_path / "tts-biglinux").mkdir(parents=True, exist_ok=True)
    return tmp_path / "tts-biglinux"


def _ins(i, backend="piper", audio=False):
    return hdb.insert_entry(
        ts=f"2026-09-15_10-00-{i:02d}-000000", backend=backend,
        voice_id="pt_BR-faber", text=f"texto {i}", text_preview=f"texto {i}",
        has_audio=audio,
    )


def test_insert_list_count(db):
    for i in range(5):
        _ins(i)
    assert hdb.count_entries() == 5
    rows = hdb.list_entries()
    assert len(rows) == 5
    # newest first (highest second)
    assert rows[0]["text_preview"] == "texto 4"
    assert all("id" in r and "timestamp" in r for r in rows)


def test_pagination(db):
    for i in range(10):
        _ins(i)
    page1 = hdb.list_entries(limit=3, offset=0)
    page2 = hdb.list_entries(limit=3, offset=3)
    assert len(page1) == 3 and len(page2) == 3
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})


def test_search(db):
    _ins(1, backend="piper")
    _ins(2, backend="kokoro")
    assert len(hdb.list_entries(query="kokoro")) == 1
    assert len(hdb.list_entries(query="texto")) == 2
    assert len(hdb.list_entries(query="zzz")) == 0


def test_delete(db):
    a = _ins(1)
    _ins(2)
    hdb.delete_entry(a)
    assert hdb.count_entries() == 1


def test_retention_max_entries(db):
    for i in range(10):
        _ins(i)
    deleted = hdb.enforce_retention(max_entries=4)
    assert hdb.count_entries() == 4
    assert len(deleted) == 6
    # kept are the newest
    kept = {r["text_preview"] for r in hdb.list_entries()}
    assert "texto 9" in kept and "texto 0" not in kept


def test_retention_max_age(db, monkeypatch):
    # old entry (ts far in the past)
    hdb.insert_entry(ts="2000-01-01_00-00-00", backend="piper", voice_id="",
                     text="old", text_preview="old", has_audio=False)
    _ins(1)  # recent-ish (2026)
    deleted = hdb.enforce_retention(max_age_days=365)
    assert any(d["text_preview"] == "old" for d in deleted)
    assert all(r["text_preview"] != "old" for r in hdb.list_entries())


def test_migrate_from_json(db):
    entries = [
        {"id": "a1", "timestamp": "2026-09-15_10-00-01-000000", "backend": "piper",
         "voice_id": "v", "text_preview": "um", "has_audio": True},
        {"id": "a2", "timestamp": "2026-09-15_10-00-02-000000", "backend": "kokoro",
         "voice_id": "w", "text_preview": "dois", "has_audio": False},
    ]
    jf = db / "history.json"
    jf.write_text(json.dumps(entries), encoding="utf-8")
    n = hdb.migrate_from_json(jf)
    assert n == 2
    assert hdb.count_entries() == 2
    assert not jf.exists()  # renamed
    assert (db / "history.json.migrated").exists()
    # idempotent-ish: migrating again (file gone) does nothing
    assert hdb.migrate_from_json(jf) == 0
