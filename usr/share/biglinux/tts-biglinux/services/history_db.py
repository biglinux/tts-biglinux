"""SQLite-backed history store.

Replaces the O(N) full-file rewrites of history.json with indexed, paginated
access and enforceable retention. Uses only the stdlib `sqlite3` (WAL mode) —
no extra dependency. Audio/text payloads stay as sibling files on disk; this DB
is the index (id, timestamp, backend, voice, preview, audio flag).
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id           TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    backend      TEXT,
    voice_id     TEXT,
    text         TEXT,
    text_preview TEXT,
    has_audio    INTEGER DEFAULT 0,
    created_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_entries_created ON entries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(ts);
"""

# Timestamp formats also understood by the history service / UI.
_TS_FMT = "%Y-%m-%d_%H-%M-%S-%f"
_TS_FMT_LEGACY = "%Y-%m-%d_%H-%M-%S"


def get_db_path() -> Path:
    """Path to the history SQLite database (under the history directory)."""
    # Lazy import avoids a circular import with history_service.
    from services.history_service import get_history_dir

    return get_history_dir() / "history.db"


def _connect() -> sqlite3.Connection:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    return conn


def _ts_to_epoch(ts: str) -> float:
    for fmt in (_TS_FMT, _TS_FMT_LEGACY):
        try:
            return datetime.strptime(ts, fmt).timestamp()
        except ValueError:
            continue
    return time.time()


def insert_entry(
    *,
    ts: str,
    backend: str,
    voice_id: str,
    text: str,
    text_preview: str,
    has_audio: bool,
    entry_id: str | None = None,
) -> str:
    """Insert one history entry. Returns its id."""
    entry_id = entry_id or uuid.uuid4().hex
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO entries "
            "(id, ts, backend, voice_id, text, text_preview, has_audio, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, ts, backend, voice_id, text, text_preview,
             1 if has_audio else 0, _ts_to_epoch(ts)),
        )
    return entry_id


def _row_to_dict(row: sqlite3.Row) -> dict:
    # Keep keys compatible with the existing UI entry dicts.
    return {
        "id": row["id"],
        "timestamp": row["ts"],
        "backend": row["backend"] or "",
        "voice_id": row["voice_id"] or "",
        "text_preview": row["text_preview"] or "",
        "has_audio": bool(row["has_audio"]),
    }


def list_entries(limit: int | None = None, offset: int = 0, query: str | None = None) -> list[dict]:
    """Return entries newest-first, optionally filtered by a search query."""
    sql = "SELECT * FROM entries"
    params: list = []
    if query:
        like = f"%{query.lower()}%"
        sql += (
            " WHERE lower(text_preview) LIKE ? OR lower(backend) LIKE ? "
            "OR lower(voice_id) LIKE ?"
        )
        params += [like, like, like]
    sql += " ORDER BY created_at DESC, ts DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_entries() -> int:
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0])


def delete_entry(entry_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))


def delete_by_ts(ts: str) -> None:
    """Delete every entry with the given timestamp (used by the legacy UI path)."""
    with _connect() as conn:
        conn.execute("DELETE FROM entries WHERE ts = ?", (ts,))


def get_by_ts(ts: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM entries WHERE ts = ?", (ts,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def enforce_retention(max_entries: int | None = None, max_age_days: int | None = None) -> list[dict]:
    """Delete entries beyond the retention policy. Returns the deleted rows
    (so the caller can remove their on-disk audio/text files).

    max_entries / max_age_days of None or <= 0 means "unlimited" for that axis.
    """
    deleted: list[dict] = []
    with _connect() as conn:
        if max_age_days and max_age_days > 0:
            cutoff = time.time() - max_age_days * 86400
            rows = conn.execute(
                "SELECT * FROM entries WHERE created_at < ?", (cutoff,)
            ).fetchall()
            deleted += [_row_to_dict(r) for r in rows]
            conn.execute("DELETE FROM entries WHERE created_at < ?", (cutoff,))
        if max_entries and max_entries > 0:
            rows = conn.execute(
                "SELECT * FROM entries ORDER BY created_at DESC, ts DESC "
                "LIMIT -1 OFFSET ?",
                (max_entries,),
            ).fetchall()
            deleted += [_row_to_dict(r) for r in rows]
            ids = [r["id"] for r in rows]
            if ids:
                conn.executemany("DELETE FROM entries WHERE id = ?", [(i,) for i in ids])
    return deleted


def migrate_from_json(json_path: Path) -> int:
    """One-time import of a legacy history.json into the DB. Returns count.

    The json file is renamed to *.migrated afterwards so it runs once.
    """
    import json

    if not json_path.exists():
        return 0
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            data = []
    except (json.JSONDecodeError, OSError):
        return 0

    migrated = 0
    with _connect() as conn:
        for e in data:
            if not isinstance(e, dict):
                continue
            ts = str(e.get("timestamp", ""))
            if not ts:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO entries "
                "(id, ts, backend, voice_id, text, text_preview, has_audio, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(e.get("id") or uuid.uuid4().hex),
                    ts,
                    str(e.get("backend", "")),
                    str(e.get("voice_id", "")),
                    "",
                    str(e.get("text_preview", "")),
                    1 if e.get("has_audio") else 0,
                    _ts_to_epoch(ts),
                ),
            )
            migrated += 1
    try:
        json_path.rename(json_path.with_suffix(".json.migrated"))
    except OSError:
        pass
    logger.info("Migrated %d history entries from JSON to SQLite", migrated)
    return migrated
