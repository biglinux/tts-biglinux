"""History service — saves spoken text and audio files."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

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
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
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

        # Append to history index
        index_file = history_dir / "history.json"
        entries = []
        if index_file.exists():
            try:
                entries = json.loads(index_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                entries = []

        entries.append(
            {
                "timestamp": timestamp,
                "backend": backend,
                "voice_id": voice_id,
                "text_preview": text[:200],
                "has_audio": save_audio and audio_path is not None,
            }
        )

        # Keep last 1000 entries
        if len(entries) > 1000:
            entries = entries[-1000:]

        index_file.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.debug("History saved: %s", base_name)

    except Exception as e:
        logger.error("Failed to save history: %s", e)
