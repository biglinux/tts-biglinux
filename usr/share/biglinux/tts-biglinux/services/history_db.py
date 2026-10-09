"""SQLite index of the reading history.

The audio (.wav) and text (.txt) of each reading are files next to the
database; this is the index (one row per reading). The files are the source of
truth for recovery: an index that is lost or damaged is rebuilt from them
(see history_service.repair_history). Stdlib `sqlite3`, WAL mode.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

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

# Columns added in schema 2 (ALTER TABLE keeps every existing row).
_COLUMNS_V2 = {
    "processed_text": "TEXT DEFAULT ''",  # what the engine was given
    "status": "TEXT DEFAULT 'completed'",  # completed | stopped | error
    "duration": "REAL DEFAULT 0",  # seconds of saved audio
    "audio_file": "TEXT DEFAULT ''",  # file name in the history folder
}

STATUSES = ("completed", "stopped", "error")

# Timestamp formats also understood by the history service / UI.
_TS_FMT = "%Y-%m-%d_%H-%M-%S-%f"
_TS_FMT_LEGACY = "%Y-%m-%d_%H-%M-%S"


def get_db_path() -> Path:
    """Path to the history SQLite database (under the history directory)."""
    # Lazy import avoids a circular import with history_service.
    from services.history_service import get_history_dir

    return get_history_dir() / "history.db"


def _migrate_schema(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return
    # Saving (worker thread) and listing (UI) may open the database at the
    # same moment: one connection migrates, the other waits and re-checks.
    level = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                have = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
                for name, decl in _COLUMNS_V2.items():
                    if name not in have:
                        conn.execute(f"ALTER TABLE entries ADD COLUMN {name} {decl}")
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = level


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """A connection that commits (or rolls back) and is always closed.

    sqlite3's own ``with conn`` only ends the transaction: the connection,
    and its db/-wal/-shm file descriptors, would stay open until garbage
    collection — one leak per saved reading.
    """
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        # WAL is stored in the file: set it once. Switching needs exclusive
        # access and fails at once ("database is locked", no busy wait) while
        # another connection is open — then that one is setting it.
        if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        _migrate_schema(conn)
        with conn:
            yield conn
    finally:
        conn.close()


def ts_to_epoch(ts: str) -> float:
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
    processed_text: str = "",
    status: str = "completed",
    duration: float = 0.0,
    audio_file: str = "",
) -> str:
    """Insert one history entry. Returns its id."""
    entry_id = entry_id or uuid.uuid4().hex
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO entries "
            "(id, ts, backend, voice_id, text, text_preview, has_audio, created_at, "
            " processed_text, status, duration, audio_file) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, ts, backend, voice_id, text, text_preview,
             1 if has_audio else 0, ts_to_epoch(ts),
             processed_text, status if status in STATUSES else "completed",
             float(duration), audio_file),
        )
    return entry_id


def _row_to_dict(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "id": row["id"],
        "timestamp": row["ts"],
        "backend": row["backend"] or "",
        "voice_id": row["voice_id"] or "",
        "text": row["text"] or "",
        "text_preview": row["text_preview"] or "",
        "has_audio": bool(row["has_audio"]),
        "created_at": row["created_at"] or 0.0,
        "processed_text": (row["processed_text"] if "processed_text" in keys else "") or "",
        "status": (row["status"] if "status" in keys else "") or "completed",
        "duration": float((row["duration"] if "duration" in keys else 0) or 0),
        "audio_file": (row["audio_file"] if "audio_file" in keys else "") or "",
    }


def _where(
    query: str | None, since: float | None, until: float | None, backend: str | None,
) -> tuple[str, list]:
    clauses, params = [], []
    if query:
        like = f"%{query.lower()}%"
        clauses.append(
            "(lower(text) LIKE ? OR lower(text_preview) LIKE ? OR lower(backend) LIKE ? "
            "OR lower(voice_id) LIKE ? OR ts LIKE ?)"
        )
        params += [like, like, like, like, like]
    if since is not None:
        clauses.append("created_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("created_at < ?")
        params.append(until)
    if backend:
        clauses.append("backend = ?")
        params.append(backend)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def list_entries(
    limit: int | None = None,
    offset: int = 0,
    query: str | None = None,
    *,
    since: float | None = None,
    until: float | None = None,
    backend: str | None = None,
    newest_first: bool = True,
) -> list[dict]:
    """Entries matching the filters, newest first (or oldest first)."""
    where, params = _where(query, since, until, backend)
    order = "DESC" if newest_first else "ASC"
    sql = f"SELECT * FROM entries{where} ORDER BY created_at {order}, ts {order}"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_entries(
    query: str | None = None,
    *,
    since: float | None = None,
    until: float | None = None,
    backend: str | None = None,
) -> int:
    where, params = _where(query, since, until, backend)
    with _connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM entries{where}", params).fetchone()[0])


def backends() -> list[str]:
    """Engines that have entries (for the filter)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT backend FROM entries WHERE backend != '' ORDER BY backend"
        ).fetchall()
    return [r[0] for r in rows]


def get_entry(entry_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_entries(entry_ids: list[str]) -> list[dict]:
    if not entry_ids:
        return []
    marks = ",".join("?" * len(entry_ids))
    with _connect() as conn:
        rows = conn.execute(f"SELECT * FROM entries WHERE id IN ({marks})", entry_ids).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_entries(entry_ids: list[str]) -> int:
    """Delete rows by id (the caller removes their files). Returns the count."""
    if not entry_ids:
        return 0
    with _connect() as conn:
        cur = conn.executemany("DELETE FROM entries WHERE id = ?", [(i,) for i in entry_ids])
        return cur.rowcount


def delete_entry(entry_id: str) -> None:
    delete_entries([entry_id])


def known_keys() -> set[tuple[str, str]]:
    """(timestamp, backend) of every row — the file names of its entry."""
    with _connect() as conn:
        return {(r[0], r[1] or "") for r in conn.execute("SELECT ts, backend FROM entries")}


def audio_rows() -> list[tuple[str, str, str, str, float]]:
    """(id, ts, backend, audio_file, duration) of the rows that claim audio."""
    with _connect() as conn:
        return [
            (r[0], r[1], r[2] or "", r[3] or "", float(r[4] or 0))
            for r in conn.execute(
                "SELECT id, ts, backend, audio_file, duration FROM entries WHERE has_audio = 1"
            )
        ]


def set_durations(durations: dict[str, float]) -> None:
    if durations:
        with _connect() as conn:
            conn.executemany(
                "UPDATE entries SET duration = ? WHERE id = ?",
                [(d, i) for i, d in durations.items()],
            )


def mark_audio_missing(entry_ids: list[str]) -> None:
    if entry_ids:
        with _connect() as conn:
            conn.executemany(
                "UPDATE entries SET has_audio = 0, audio_file = '' WHERE id = ?",
                [(i,) for i in entry_ids],
            )


def integrity_ok() -> bool:
    """False if the database file is damaged (it must then be rebuilt)."""
    path = get_db_path()
    if not path.exists():
        return True
    try:
        conn = sqlite3.connect(str(path), timeout=10)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
        return bool(row) and row[0] == "ok"
    except sqlite3.DatabaseError as e:
        logger.warning("History database damaged: %s", e)
        return False


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

    Entries already indexed (same timestamp and engine) are skipped, so an
    interrupted migration can run again without duplicates. The JSON file is
    renamed to *.migrated afterwards and never deleted.
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

    known = known_keys()
    migrated = 0
    with _connect() as conn:
        for e in data:
            if not isinstance(e, dict):
                continue
            ts = str(e.get("timestamp", ""))
            backend = str(e.get("backend", ""))
            if not ts or (ts, backend) in known:
                continue
            preview = str(e.get("text_preview", ""))
            conn.execute(
                "INSERT INTO entries "
                "(id, ts, backend, voice_id, text, text_preview, has_audio, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(e.get("id") or uuid.uuid4().hex),
                    ts,
                    backend,
                    str(e.get("voice_id", "")),
                    str(e.get("text", "")) or preview,
                    preview,
                    1 if e.get("has_audio") else 0,
                    ts_to_epoch(ts),
                ),
            )
            known.add((ts, backend))
            migrated += 1
    try:
        json_path.rename(json_path.with_suffix(".json.migrated"))
    except OSError:
        pass
    logger.info("Migrated %d history entries from JSON to SQLite", migrated)
    return migrated
