"""Diagnostics collector — gathers environment/backend info for support.

Pure data gathering (no UI): returns a structured dict and a human-readable
text block suitable for a "Copy diagnostic" button.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Backends detected by the presence of their CLI/binary.
_BACKEND_BINARIES = {
    "espeak-ng": "espeak-ng",
    "piper-tts": "piper-tts",
    "koko (Kokoro)": "koko",
    "RHVoice": "RHVoice-test",
    "speech-dispatcher": "spd-say",
    "sox": "sox",
    "aplay (ALSA)": "aplay",
    "pw-play (PipeWire)": "pw-play",
}


def _native_engine_version() -> str:
    try:
        import tts_engine

        return tts_engine.version()
    except Exception:
        return "unavailable"


def _onnxruntime_version() -> str:
    """Resolve the system libonnxruntime version from its .so symlink target."""
    for base in ("/usr/lib", "/usr/lib64"):
        so = Path(base) / "libonnxruntime.so.1"
        try:
            if so.exists():
                target = os.path.realpath(so)
                # e.g. libonnxruntime.so.1.29.0 → 1.29.0
                name = Path(target).name
                ver = name.split(".so.", 1)[-1] if ".so." in name else ""
                return ver or target
        except OSError:
            continue
    return "not found"


def _count_piper_voices() -> int:
    count = 0
    for base in (
        "/usr/share/piper-voices",
        "/usr/local/share/piper-voices",
        str(Path.home() / ".local/share/piper-voices"),
    ):
        p = Path(base)
        if p.is_dir():
            count += sum(1 for _ in p.rglob("*.onnx"))
    return count


def collect_diagnostics(settings=None) -> dict:
    """Collect a structured diagnostics snapshot."""
    from config import APP_VERSION

    backends = {
        label: (shutil.which(binary) or "") for label, binary in _BACKEND_BINARIES.items()
    }

    active_backend = ""
    active_voice = ""
    if settings is not None:
        speech = getattr(settings, "speech", None)
        if speech is not None:
            active_backend = getattr(speech, "backend", "")
            active_voice = getattr(speech, "voice_id", "")

    return {
        "app_version": APP_VERSION,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "native_engine": _native_engine_version(),
        "onnxruntime": _onnxruntime_version(),
        "backends": backends,
        "piper_voices_installed": _count_piper_voices(),
        "active_backend": active_backend,
        "active_voice": active_voice,
        "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
    }


def format_diagnostics(data: dict) -> str:
    """Render the diagnostics dict as a copy-pastable text block."""
    lines = [
        "BigLinux TTS — Diagnostic",
        f"App version:      {data.get('app_version')}",
        f"Python:           {data.get('python_version')}",
        f"Platform:         {data.get('platform')}",
        f"Session/Desktop:  {data.get('session_type')} / {data.get('desktop')}",
        f"Native engine:    {data.get('native_engine')}",
        f"ONNX Runtime:     {data.get('onnxruntime')}",
        f"Piper voices:     {data.get('piper_voices_installed')}",
        f"Active backend:   {data.get('active_backend') or '(none)'}",
        f"Active voice:     {data.get('active_voice') or '(none)'}",
        "Backends detected:",
    ]
    for label, path in data.get("backends", {}).items():
        lines.append(f"  - {label:22} {'✔ ' + path if path else '✘ not found'}")
    return "\n".join(lines)
