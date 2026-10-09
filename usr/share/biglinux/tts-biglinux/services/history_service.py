"""Reading history: the audio and text of each reading, and their index.

Layout of the history folder (XDG Music/tts-biglinux, private 0700):

    <timestamp>_<engine>.wav   the audio that was played (when kept)
    <timestamp>_<engine>.txt   the text that was read (when kept)
    history.db                 SQLite index (history_db), rebuilt from the
                               files if it is lost or damaged

Recording: the audio goes straight into ``.rec-<timestamp>_<engine>.wav.part``
in this folder while it plays (no copy at the end) and becomes the final
file when the reading ends. Deleting moves the files to the desktop's Trash.
Saving never raises: a failure is logged and playback is never affected.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import struct
import threading
import time
import uuid
import weakref
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Timestamp of an entry = its file base name. Microsecond resolution (%f):
# two readings saved in the same second must not share file names.
TIMESTAMP_FMT = "%Y-%m-%d_%H-%M-%S-%f"
# Second-resolution format of entries saved by older versions (still parsed).
LEGACY_TIMESTAMP_FMT = "%Y-%m-%d_%H-%M-%S"

AUDIO_EXTENSIONS = (".wav", ".mp3", ".ogg", ".flac")
MIN_AUDIO_SECONDS = 0.1
_RE_ENTRY_FILE = re.compile(
    r"^(\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d(?:-\d{6})?)_([a-z0-9-]+)(\.wav|\.mp3|\.ogg|\.flac|\.txt)$"
)
_RE_PART_FILE = re.compile(r"^\.rec-(\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d-\d{6})_([a-z0-9-]+)\.wav\.part$")

# XDG Music directory detection
_MUSIC_DIR: Path | None = None


def _get_music_dir() -> Path:
    """Get the user's Music directory (XDG or fallback)."""
    global _MUSIC_DIR
    if _MUSIC_DIR is not None:
        return _MUSIC_DIR

    try:
        import subprocess

        proc = subprocess.run(
            ["xdg-user-dir", "MUSIC"], capture_output=True, text=True, timeout=3,
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


def display_history_dir() -> str:
    """The history folder as shown to people ("~/Music/tts-biglinux")."""
    path, home = str(get_history_dir()), str(Path.home())
    return "~" + path[len(home):] if path.startswith(home + "/") else path


def ensure_history_dir() -> Path:
    """Ensure the history directory exists (private: 0700) and return it."""
    history_dir = get_history_dir()
    history_dir.mkdir(parents=True, exist_ok=True)
    try:
        history_dir.chmod(0o700)  # what was read aloud is personal
    except OSError as e:
        logger.debug("Could not restrict %s: %s", history_dir, e)
    return history_dir


def new_timestamp(when: float | None = None) -> str:
    return datetime.fromtimestamp(when if when is not None else time.time()).strftime(TIMESTAMP_FMT)


def parse_timestamp(timestamp: str) -> datetime | None:
    for fmt in (TIMESTAMP_FMT, LEGACY_TIMESTAMP_FMT):
        try:
            return datetime.strptime(timestamp, fmt)
        except ValueError:
            continue
    return None


# ── Change notifications (for the History view) ─────────────────────

_listeners: list = []  # weak references: a closed view is never called


def add_listener(callback: Callable[[], None]) -> None:
    """Call ``callback`` on the GTK main thread whenever the history changes."""
    ref = weakref.WeakMethod(callback) if hasattr(callback, "__self__") else (lambda: callback)
    _listeners.append(ref)


def remove_listener(callback: Callable[[], None]) -> None:
    _listeners[:] = [r for r in _listeners if r() not in (None, callback)]


def _notify() -> None:
    try:
        from gi.repository import GLib
    except ImportError:
        return
    _listeners[:] = [r for r in _listeners if r() is not None]
    for ref in list(_listeners):
        GLib.idle_add(lambda ref=ref: (cb := ref()) is not None and cb() and False)


# ── WAV files ─────────────────────────────────────────────────────────

def wav_layout(head: bytes) -> tuple[int, int] | None:
    """(byte rate, offset of the data payload) of a WAV header, or None."""
    fmt = _wav_format(head)
    return (fmt[0], fmt[2]) if fmt else None


def _wav_format(head: bytes) -> tuple[int, int, int] | None:
    """(byte rate, block align, data offset) of a WAV header, or None."""
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None
    pos, byte_rate, align = 12, 0, 1
    while pos + 8 <= len(head):
        chunk, size = head[pos:pos + 4], struct.unpack("<I", head[pos + 4:pos + 8])[0]
        if chunk == b"fmt " and pos + 22 <= len(head):
            byte_rate, align = struct.unpack("<IH", head[pos + 16:pos + 22])
        if chunk == b"data":
            return (byte_rate, max(1, align), pos + 8) if byte_rate else None
        pos += 8 + size + (size & 1)
    return None


def wav_duration(path: Path) -> float:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        layout = wav_layout(head)
        if not layout:
            return 0.0
        return max(0, path.stat().st_size - layout[1]) / layout[0]
    except OSError:
        return 0.0


def finalize_wav(path: Path, max_seconds: float | None = None) -> float:
    """Write the real sizes into a WAV whose header says "unknown" (streamed
    audio) and return its duration in seconds (0 if it holds no audio).

    ``max_seconds`` cuts the audio there: engines synthesize ahead of what is
    heard, so a stopped reading keeps only what was played.
    """
    try:
        with open(path, "r+b") as f:
            head = f.read(4096)
            fmt = _wav_format(head)
            if not fmt:
                return 0.0
            byte_rate, align, data_off = fmt
            size = os.fstat(f.fileno()).st_size
            data_len = max(0, size - data_off)
            data_len -= data_len % align  # a partial sample at the end
            if max_seconds is not None:
                data_len = min(data_len, int(max_seconds * byte_rate) // align * align)
            if data_off + data_len < size:
                f.truncate(data_off + data_len)
                size = data_off + data_len
            f.seek(4)
            f.write(struct.pack("<I", min(size - 8, 0xFFFFFFFF)))
            f.seek(data_off - 4)
            f.write(struct.pack("<I", min(data_len, 0xFFFFFFFF)))
        return data_len / byte_rate
    except OSError as e:
        logger.warning("Could not finish %s: %s", path, e)
        return 0.0


class Recording:
    """The audio of one reading, written into the history folder as it plays.

    Engines that stream a WAV (RHVoice, Kokoro) pass the bytes they send to the
    player to :meth:`write`; engines that play one WAV file per chunk
    (espeak-ng, Piper) pass each file to :meth:`add_wav`. Thread-safe; after
    :meth:`close` further audio is ignored.
    """

    def __init__(self, path: Path, *, external: bool = False) -> None:
        """``external``: the engine writes ``path`` itself (Kokoro's -o)."""
        self.path = path
        self._file = None if external else open(path, "wb")  # noqa: SIM115 — closed in close()
        self._lock = threading.Lock()
        self._closed = False
        self._has_header = False
        self.failed = False

    def write(self, data: bytes) -> None:
        with self._lock:
            if self._closed or self.failed:
                return
            try:
                self._file.write(data)
                self._has_header = True
            except OSError as e:  # disk full, removed folder…: stop recording only
                logger.warning("History recording stopped: %s", e)
                self.failed = True

    def add_wav(self, wav_path: str) -> None:
        try:
            data = Path(wav_path).read_bytes()
        except OSError:
            return
        with self._lock:
            if self._closed or self.failed:
                return
            if self._has_header:
                layout = wav_layout(data[:4096])
                if not layout:
                    return
                data = data[layout[1]:]  # same format: append the samples only
            try:
                self._file.write(data)
                self._has_header = True
            except OSError as e:
                logger.warning("History recording stopped: %s", e)
                self.failed = True

    def close(self, max_seconds: float | None = None) -> float:
        """Finish the file; returns the duration (0: no usable audio)."""
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    if self._file is not None:
                        self._file.close()
                except OSError:
                    self.failed = True
        if self.failed or not self.path.exists():
            return 0.0
        return finalize_wav(self.path, max_seconds)

    def discard(self) -> None:
        self.close()
        _remove(self.path)


def new_recording(backend: str, started: float, *, external: bool = False) -> Recording | None:
    """A recording for a reading that starts now, or None if impossible.

    ``external``: the engine writes the file itself (its path is
    ``recording.path``).
    """
    try:
        d = ensure_history_dir()
        return Recording(d / f".rec-{new_timestamp(started)}_{backend}.wav.part", external=external)
    except OSError as e:
        logger.warning("History audio cannot be recorded in %s: %s", get_history_dir(), e)
        return None


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Could not remove %s: %s", path, e)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── Saving ────────────────────────────────────────────────────────────

def save_reading(
    *,
    text: str,
    backend: str,
    voice_id: str,
    started: float | None = None,
    processed_text: str = "",
    status: str = "completed",
    recording: Recording | None = None,
    heard_seconds: float | None = None,
    save_audio: bool = True,
    save_text: bool = True,
    max_entries: int = 0,
    max_age_days: int = 0,
) -> dict | None:
    """Store one reading. Returns the new entry, or None if nothing was kept.

    ``heard_seconds`` (a stopped reading) cuts the audio to what was played.
    Blocking (file and database work): call it from a worker thread.
    """
    from services import history_db

    duration = recording.close(heard_seconds) if recording else 0.0
    # A reading stopped at its very first samples leaves a useless file.
    keep_audio = bool(recording and save_audio and duration >= MIN_AUDIO_SECONDS)
    if recording and not keep_audio:
        _remove(recording.path)
    if not keep_audio and not save_text:
        return None
    try:
        history_dir = ensure_history_dir()
        _ensure_migrated(history_dir)
        when = started if started is not None else time.time()
        ts = new_timestamp(when)
        while any((history_dir / f"{ts}_{backend}{ext}").exists() for ext in (".wav", ".txt")):
            when += 0.000001  # an entry with this name exists: never overwrite it
            ts = new_timestamp(when)
        base = f"{ts}_{backend}"
        audio_file = ""
        if keep_audio and recording:
            audio_file = f"{base}.wav"
            os.replace(recording.path, history_dir / audio_file)
        if save_text:
            _write_atomic(history_dir / f"{base}.txt", text)
        entry_id = history_db.insert_entry(
            ts=ts,
            backend=backend,
            voice_id=voice_id,
            text=text if save_text else "",
            text_preview=text[:200] if save_text else "",
            has_audio=bool(audio_file),
            processed_text=processed_text if save_text else "",
            status=status,
            duration=duration if audio_file else 0.0,
            audio_file=audio_file,
        )
        logger.debug("History saved: %s (%s)", base, status)
        if (max_entries and max_entries > 0) or (max_age_days and max_age_days > 0):
            apply_history_retention(max_entries=max_entries or None, max_age_days=max_age_days or None)
        _notify()
        return history_db.get_entry(entry_id)
    except Exception as e:  # never let history break a reading
        logger.error("Failed to save history: %s", e)
        if recording:
            _remove(recording.path)
        return None


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
    """Store a reading whose audio is an existing WAV file (copied)."""
    recording = None
    if save_audio and audio_path and os.path.isfile(audio_path):
        recording = new_recording(backend, time.time())
        if recording:
            recording.add_wav(audio_path)
    save_reading(
        text=text, backend=backend, voice_id=voice_id, recording=recording,
        save_audio=save_audio, save_text=save_text,
        max_entries=max_entries, max_age_days=max_age_days,
    )


# ── Reading ───────────────────────────────────────────────────────────

def _ensure_migrated(history_dir: Path) -> None:
    """Migrate a legacy history.json into SQLite exactly once."""
    from services import history_db

    json_path = history_dir / "history.json"
    if json_path.exists():
        history_db.migrate_from_json(json_path)


def load_history_entries(
    limit: int | None = None, offset: int = 0, query: str | None = None, **filters
) -> list[dict]:
    """Entries (newest first unless ``newest_first=False``), with filters."""
    from services import history_db

    _ensure_migrated(ensure_history_dir())
    return history_db.list_entries(limit=limit, offset=offset, query=query, **filters)


def count_history_entries(query: str | None = None, **filters) -> int:
    from services import history_db

    _ensure_migrated(ensure_history_dir())
    return history_db.count_entries(query, **filters)


def audio_path(entry: dict) -> Path | None:
    """The entry's audio file, if it still exists."""
    d = get_history_dir()
    names = [entry["audio_file"]] if entry.get("audio_file") else []
    names += [f"{entry.get('timestamp', '')}_{entry.get('backend', '')}{ext}" for ext in AUDIO_EXTENSIONS]
    for name in names:
        path = d / name
        if path.is_file():
            return path
    return None


def entry_text(entry: dict) -> str:
    """The full text of an entry (index, or its .txt file)."""
    if entry.get("text"):
        return entry["text"]
    path = get_history_dir() / f"{entry.get('timestamp', '')}_{entry.get('backend', '')}.txt"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return entry.get("text_preview", "")


def _entry_files(entry: dict) -> list[Path]:
    d = get_history_dir()
    base = f"{entry.get('timestamp', '')}_{entry.get('backend', '')}"
    files = [d / f"{base}{ext}" for ext in (*AUDIO_EXTENSIONS, ".txt")]
    if entry.get("audio_file"):
        files.append(d / entry["audio_file"])
    return [p for p in dict.fromkeys(files) if p.is_file()]


def _trash(path: Path) -> None:
    """Move to the desktop's Trash (recoverable); delete if there is none."""
    try:
        from gi.repository import Gio

        Gio.File.new_for_path(str(path)).trash(None)
        return
    except Exception as e:  # no Trash on this file system / no GIO
        logger.debug("Trash unavailable for %s (%s): deleting", path, e)
    _remove(path)


def delete_entries(entry_ids: list[str]) -> int:
    """Delete entries: their files go to the Trash, then the index rows.

    Files restored from the Trash come back as entries on the next start
    (see repair_history). Returns how many entries were deleted.
    """
    from services import history_db

    rows = history_db.get_entries(entry_ids)
    for row in rows:
        for path in _entry_files(row):
            _trash(path)
    count = history_db.delete_entries([r["id"] for r in rows])
    _notify()
    return count


def export_audio(entry: dict, destination: str) -> bool:
    path = audio_path(entry)
    if path is None:
        return False
    try:
        shutil.copyfile(path, destination)
        return True
    except OSError as e:
        logger.warning("Could not export %s: %s", path, e)
        return False


def apply_history_retention(
    max_entries: int | None = None, max_age_days: int | None = None
) -> None:
    """Enforce the retention the user configured (files are deleted)."""
    from services import history_db

    deleted = history_db.enforce_retention(max_entries=max_entries, max_age_days=max_age_days)
    for row in deleted:
        for path in _entry_files(row):
            _remove(path)


# ── Recovery ──────────────────────────────────────────────────────────

def repair_history() -> dict:
    """Bring the index in line with the files; never deletes a recording.

    - a damaged database is set aside (``history.db.damaged-<time>``) and the
      index is rebuilt from the files;
    - a legacy history.json is migrated (once, without duplicates);
    - audio/text files without an index row (restored from the Trash, an
      older version, a lost index) become entries again;
    - a recording interrupted by a crash (``.rec-…part``) is finished and
      recovered as audio;
    - rows whose audio file is gone stay, marked as text only.

    Blocking: run it in a worker thread (the app does at startup).
    """
    from services import history_db

    report = {"rebuilt": False, "migrated": 0, "recovered": 0, "audio_missing": 0, "error": ""}
    history_dir = get_history_dir()
    if not history_dir.is_dir():
        return report
    try:
        if not history_db.integrity_ok():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            for suffix in ("", "-wal", "-shm"):
                src = history_dir / f"history.db{suffix}"
                if src.exists():
                    os.replace(src, history_dir / f"history.db.damaged-{stamp}{suffix}")
            report["rebuilt"] = True
            logger.warning("History index was damaged: kept as history.db.damaged-%s, rebuilt", stamp)

        json_path = history_dir / "history.json"
        if json_path.exists():
            report["migrated"] = history_db.migrate_from_json(json_path)

        # Interrupted recordings (the app stopped while playing).
        for part in history_dir.glob(".rec-*.wav.part"):
            m = _RE_PART_FILE.match(part.name)
            if not m or time.time() - part.stat().st_mtime < 600:
                continue  # unknown name, or maybe still being written
            target = history_dir / f"{m.group(1)}_{m.group(2)}.wav"
            if finalize_wav(part) > 0 and not target.exists():
                os.replace(part, target)

        known = history_db.known_keys()
        found: dict[tuple[str, str], dict[str, str]] = {}
        for path in history_dir.iterdir():
            m = _RE_ENTRY_FILE.match(path.name)
            if m:
                found.setdefault((m.group(1), m.group(2)), {})[m.group(3)] = path.name
        for (ts, backend), files in sorted(found.items()):
            if (ts, backend) in known:
                continue
            text = ""
            if ".txt" in files:
                try:
                    text = (history_dir / files[".txt"]).read_text(encoding="utf-8")
                except OSError:
                    pass
            audio = next((files[e] for e in AUDIO_EXTENSIONS if e in files), "")
            history_db.insert_entry(
                ts=ts, backend=backend, voice_id="", text=text, text_preview=text[:200],
                has_audio=bool(audio), audio_file=audio,
                duration=wav_duration(history_dir / audio) if audio.endswith(".wav") else 0.0,
            )
            report["recovered"] += 1

        missing, durations = [], {}
        for entry_id, ts, backend, audio_file, duration in history_db.audio_rows():
            path = audio_path({"timestamp": ts, "backend": backend, "audio_file": audio_file})
            if path is None:
                missing.append(entry_id)
            elif not duration and path.suffix == ".wav":
                durations[entry_id] = wav_duration(path)  # entries of older versions
        history_db.mark_audio_missing(missing)
        history_db.set_durations(durations)
        report["audio_missing"] = len(missing)
    except Exception as e:  # permissions, read-only disk…: report, never crash
        logger.error("History repair failed: %s", e)
        report["error"] = str(e)
    if report["recovered"] or report["migrated"] or report["rebuilt"]:
        logger.info("History repair: %s", report)
        _notify()
    return report


def new_entry_id() -> str:
    return uuid.uuid4().hex
