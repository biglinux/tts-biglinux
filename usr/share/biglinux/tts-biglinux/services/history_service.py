"""History service — saves spoken text and audio files."""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Timestamp format used for history entry ids / on-disk file base names.
# Microsecond resolution (%f) guarantees uniqueness: two entries generated in
# the same second no longer collide and overwrite each other's files.
TIMESTAMP_FMT = "%Y-%m-%d_%H-%M-%S-%f"
# Legacy second-resolution format (pre-fix entries) — still parsed for display.
LEGACY_TIMESTAMP_FMT = "%Y-%m-%d_%H-%M-%S"

# XDG Music directory detection
_MUSIC_DIR: Path | None = None


def _get_music_dir() -> Path:
    """Get the user's Music directory (XDG or fallback)."""
    global _MUSIC_DIR
    if _MUSIC_DIR is not None:
        return _MUSIC_DIR

    # Try XDG user dirs
    try:
        import subprocess

        proc = subprocess.run(
            ["xdg-user-dir", "MUSIC"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            _MUSIC_DIR = Path(proc.stdout.strip())
            return _MUSIC_DIR
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    _MUSIC_DIR = Path.home() / "Music"
    return _MUSIC_DIR


def get_history_dir() -> Path:
    """Get the history directory path."""
    return _get_music_dir() / "tts-biglinux"


def ensure_history_dir() -> Path:
    """Ensure the history directory exists and return its path."""
    history_dir = get_history_dir()
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir


def save_history_entry(
    text: str,
    audio_path: str | None,
    backend: str,
    voice_id: str,
    save_audio: bool = True,
    save_text: bool = True,
    max_entries: int = 0,
    max_age_days: int = 0,
) -> None:
    """Save a history entry (text + optional audio copy).

    Args:
        text: The spoken text
        audio_path: Path to the generated audio file (temp)
        backend: TTS backend used
        voice_id: Voice ID used
        save_audio: Whether to copy the audio file
        save_text: Whether to save the text
    """
    try:
        history_dir = ensure_history_dir()
        timestamp = datetime.now().strftime(TIMESTAMP_FMT)
        entry_id = uuid.uuid4().hex
        base_name = f"{timestamp}_{backend}"

        # Save text
        if save_text:
            text_file = history_dir / f"{base_name}.txt"
            text_file.write_text(text, encoding="utf-8")

        # Copy audio
        if save_audio and audio_path and os.path.isfile(audio_path):
            audio_ext = Path(audio_path).suffix or ".wav"
            audio_dest = history_dir / f"{base_name}{audio_ext}"
            shutil.copy2(audio_path, audio_dest)

        # Index into SQLite (O(1) insert, indexed) instead of rewriting a JSON
        # file on every save. Migrate any legacy history.json first.
        from services import history_db

        _ensure_migrated(history_dir)
        history_db.insert_entry(
            ts=timestamp,
            backend=backend,
            voice_id=voice_id,
            text=text,
            text_preview=text[:200],
            has_audio=save_audio and audio_path is not None,
            entry_id=entry_id,
        )
        logger.debug("History saved: %s", base_name)

        # Enforce retention (never deletes outside the configured limits).
        if (max_entries and max_entries > 0) or (max_age_days and max_age_days > 0):
            apply_history_retention(
                max_entries=max_entries or None,
                max_age_days=max_age_days or None,
            )

    except Exception as e:
        logger.error("Failed to save history: %s", e)


def _ensure_migrated(history_dir: Path) -> None:
    """Migrate a legacy history.json into SQLite exactly once."""
    from services import history_db

    json_path = history_dir / "history.json"
    if json_path.exists():
        history_db.migrate_from_json(json_path)


def load_history_entries(
    limit: int | None = None, offset: int = 0, query: str | None = None
) -> list[dict]:
    """Return history entries (newest first) from SQLite, migrating JSON first."""
    from services import history_db

    _ensure_migrated(ensure_history_dir())
    return history_db.list_entries(limit=limit, offset=offset, query=query)


def count_history_entries() -> int:
    from services import history_db

    _ensure_migrated(ensure_history_dir())
    return history_db.count_entries()


def _delete_entry_files(history_dir: Path, ts: str, backend: str) -> None:
    base = f"{ts}_{backend}"
    for ext in (".wav", ".mp3", ".ogg", ".flac", ".txt"):
        path = history_dir / f"{base}{ext}"
        try:
            if path.is_file():
                os.remove(path)
        except OSError as e:
            logger.warning("Failed to delete %s: %s", path, e)


def delete_history_by_ts(ts: str, backend: str = "") -> None:
    """Delete history entries with the given timestamp plus their files."""
    from services import history_db

    history_dir = get_history_dir()
    rows = history_db.get_by_ts(ts)
    backends = {r.get("backend", "") for r in rows} or {backend}
    for b in backends:
        _delete_entry_files(history_dir, ts, b)
    history_db.delete_by_ts(ts)


def apply_history_retention(
    max_entries: int | None = None, max_age_days: int | None = None
) -> None:
    """Enforce retention policy and delete the associated files."""
    from services import history_db

    _ensure_migrated(ensure_history_dir())
    deleted = history_db.enforce_retention(
        max_entries=max_entries, max_age_days=max_age_days
    )
    history_dir = get_history_dir()
    for row in deleted:
        _delete_entry_files(history_dir, row.get("timestamp", ""), row.get("backend", ""))
