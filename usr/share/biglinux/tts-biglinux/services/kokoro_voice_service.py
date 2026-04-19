"""
Kokoro Voice Service — individual voice download/removal for Kokoro TTS.

Manages voice .npy files independently: downloads .pt from HuggingFace,
converts to .npy, and merges into a user-local voices.bin ZIP that
supplements the system-provided base voices.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import NamedTuple
from urllib.request import Request, urlopen
from urllib.error import URLError

import numpy as np

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────

SYSTEM_VOICES_BIN = Path("/usr/share/biglinux-kokoro-tts/voices/voices.bin")
USER_VOICES_DIR = Path.home() / ".local" / "share" / "biglinux-tts" / "kokoro-voices"
USER_VOICES_BIN = USER_VOICES_DIR / "voices.bin"

HF_REPO = "hexgrad/Kokoro-82M"
HF_BASE_URL = f"https://huggingface.co/{HF_REPO}/resolve/main/voices"

# Shape of each Kokoro voice style vector
_VOICE_SHAPE = (510, 1, 256)
_VOICE_DTYPE = np.float32


# ── Voice catalog ────────────────────────────────────────────────────

class KokoroVoiceEntry(NamedTuple):
    voice_id: str
    name: str
    language: str
    gender: str
    description: str


# Base voices shipped with biglinux-kokoro-tts package (not removable)
BASE_VOICE_IDS = frozenset({
    "af_heart", "ef_dora", "ff_siwis", "hf_alpha",
    "if_sara", "jf_alpha", "pf_dora", "zf_xiaobei",
})

# Full catalog of all 54 Kokoro voices
KOKORO_CATALOG: tuple[KokoroVoiceEntry, ...] = (
    # Brazilian Portuguese
    KokoroVoiceEntry("pf_dora", "Dora", "pt-BR", "female", "Voz feminina neural em pt-BR"),
    KokoroVoiceEntry("pm_alex", "Alex", "pt-BR", "male", "Voz masculina neural em pt-BR"),
    KokoroVoiceEntry("pm_santa", "Santa", "pt-BR", "male", "Voz masculina alternativa pt-BR"),
    # American English
    KokoroVoiceEntry("af_heart", "Heart ❤️", "en-US", "female", "Primary reference voice (best quality)"),
    KokoroVoiceEntry("af_alloy", "Alloy", "en-US", "female", "Alloy feminine voice"),
    KokoroVoiceEntry("af_aoede", "Aoede", "en-US", "female", "Clear feminine voice"),
    KokoroVoiceEntry("af_bella", "Bella", "en-US", "female", "Warm feminine voice"),
    KokoroVoiceEntry("af_jessica", "Jessica", "en-US", "female", "Expressive feminine voice"),
    KokoroVoiceEntry("af_kore", "Kore", "en-US", "female", "Bright feminine voice"),
    KokoroVoiceEntry("af_nicole", "Nicole", "en-US", "female", "Calm feminine voice"),
    KokoroVoiceEntry("af_nova", "Nova", "en-US", "female", "Modern feminine voice"),
    KokoroVoiceEntry("af_river", "River", "en-US", "female", "Natural feminine voice"),
    KokoroVoiceEntry("af_sarah", "Sarah", "en-US", "female", "Neutral feminine voice"),
    KokoroVoiceEntry("af_sky", "Sky", "en-US", "female", "Youthful feminine voice"),
    KokoroVoiceEntry("am_adam", "Adam", "en-US", "male", "Deep masculine voice"),
    KokoroVoiceEntry("am_echo", "Echo", "en-US", "male", "Clear masculine voice"),
    KokoroVoiceEntry("am_eric", "Eric", "en-US", "male", "Professional masculine voice"),
    KokoroVoiceEntry("am_fenrir", "Fenrir", "en-US", "male", "Strong masculine voice"),
    KokoroVoiceEntry("am_fable", "Fable", "en-US", "male", "Narrative masculine voice"),
    KokoroVoiceEntry("am_liam", "Liam", "en-US", "male", "Friendly masculine voice"),
    KokoroVoiceEntry("am_michael", "Michael", "en-US", "male", "Neutral masculine voice"),
    KokoroVoiceEntry("am_onyx", "Onyx", "en-US", "male", "Deep masculine voice"),
    KokoroVoiceEntry("am_puck", "Puck", "en-US", "male", "Playful masculine voice"),
    KokoroVoiceEntry("am_santa", "Santa", "en-US", "male", "Warm masculine voice"),
    # British English
    KokoroVoiceEntry("bf_alice", "Alice", "en-GB", "female", "British feminine voice"),
    KokoroVoiceEntry("bf_emma", "Emma", "en-GB", "female", "British feminine voice"),
    KokoroVoiceEntry("bf_isabella", "Isabella", "en-GB", "female", "British feminine voice"),
    KokoroVoiceEntry("bf_lily", "Lily", "en-GB", "female", "British feminine voice"),
    KokoroVoiceEntry("bm_daniel", "Daniel", "en-GB", "male", "British masculine voice"),
    KokoroVoiceEntry("bm_fable", "Fable", "en-GB", "male", "British masculine voice"),
    KokoroVoiceEntry("bm_george", "George", "en-GB", "male", "British masculine voice"),
    KokoroVoiceEntry("bm_lewis", "Lewis", "en-GB", "male", "British masculine voice"),
    # Spanish
    KokoroVoiceEntry("ef_dora", "Dora", "es", "female", "Spanish feminine voice"),
    KokoroVoiceEntry("em_alex", "Alex", "es", "male", "Spanish masculine voice"),
    KokoroVoiceEntry("em_santa", "Santa", "es", "male", "Spanish masculine voice"),
    # French
    KokoroVoiceEntry("ff_siwis", "Siwis", "fr", "female", "French feminine voice"),
    # Italian
    KokoroVoiceEntry("if_sara", "Sara", "it", "female", "Italian feminine voice"),
    KokoroVoiceEntry("im_nicola", "Nicola", "it", "male", "Italian masculine voice"),
    # Japanese
    KokoroVoiceEntry("jf_alpha", "Alpha", "ja", "female", "Japanese feminine voice"),
    KokoroVoiceEntry("jf_gongitsune", "Gongitsune", "ja", "female", "Japanese feminine voice"),
    KokoroVoiceEntry("jf_nezumi", "Nezumi", "ja", "female", "Japanese feminine voice"),
    KokoroVoiceEntry("jf_tebukuro", "Tebukuro", "ja", "female", "Japanese feminine voice"),
    KokoroVoiceEntry("jm_kumo", "Kumo", "ja", "male", "Japanese masculine voice"),
    # Hindi
    KokoroVoiceEntry("hf_alpha", "Alpha", "hi", "female", "Hindi feminine voice"),
    KokoroVoiceEntry("hf_beta", "Beta", "hi", "female", "Hindi feminine voice"),
    KokoroVoiceEntry("hm_omega", "Omega", "hi", "male", "Hindi masculine voice"),
    KokoroVoiceEntry("hm_psi", "Psi", "hi", "male", "Hindi masculine voice"),
    # Mandarin Chinese
    KokoroVoiceEntry("zf_xiaobei", "Xiaobei", "zh", "female", "Chinese feminine voice"),
    KokoroVoiceEntry("zf_xiaoni", "Xiaoni", "zh", "female", "Chinese feminine voice"),
    KokoroVoiceEntry("zf_xiaoxiao", "Xiaoxiao", "zh", "female", "Chinese feminine voice"),
    KokoroVoiceEntry("zf_xiaoyi", "Xiaoyi", "zh", "female", "Chinese feminine voice"),
    KokoroVoiceEntry("zm_yunjian", "Yunjian", "zh", "male", "Chinese masculine voice"),
    KokoroVoiceEntry("zm_yunxi", "Yunxi", "zh", "male", "Chinese masculine voice"),
    KokoroVoiceEntry("zm_yunxia", "Yunxia", "zh", "male", "Chinese masculine voice"),
    KokoroVoiceEntry("zm_yunyang", "Yunyang", "zh", "male", "Chinese masculine voice"),
)

_CATALOG_BY_ID: dict[str, KokoroVoiceEntry] = {v.voice_id: v for v in KOKORO_CATALOG}


# ── Public API ───────────────────────────────────────────────────────

def is_kokoro_installed() -> bool:
    """Check if the Kokoro Python library is importable."""
    try:
        import kokoro  # noqa: F401
        return True
    except ImportError:
        return False


def get_installed_voice_ids() -> set[str]:
    """Return set of voice IDs available.

    With the Python API, all catalog voices are available on demand
    (KPipeline downloads from HuggingFace on first use).
    Falls back to voices.bin if present.
    """
    if is_kokoro_installed():
        return {v.voice_id for v in KOKORO_CATALOG}
    voices_bin = _active_voices_bin()
    if not voices_bin.exists():
        return set()
    try:
        with zipfile.ZipFile(voices_bin, "r") as z:
            return {name.removesuffix(".npy") for name in z.namelist() if name.endswith(".npy")}
    except (zipfile.BadZipFile, OSError) as e:
        logger.error("Failed to read voices.bin: %s", e)
        return set()


def get_voice_status() -> list[dict[str, str]]:
    """Return Kokoro voices with install status for the Voice Manager UI.

    Returns list of dicts compatible with VoiceManagerDialog's package format:
        pkg, display_name, language, engine, gender, installed, voice_id, is_base
    """
    if not is_kokoro_installed():
        return []

    installed = get_installed_voice_ids()
    result: list[dict[str, str]] = []

    for entry in KOKORO_CATALOG:
        is_installed = entry.voice_id in installed
        is_base = entry.voice_id in BASE_VOICE_IDS
        result.append({
            "pkg": f"kokoro-voice-{entry.voice_id}",
            "display_name": entry.name,
            "language": entry.language,
            "engine": "Kokoro",
            "gender": entry.gender,
            "installed": "yes" if is_installed else "no",
            "voice_id": entry.voice_id,
            "is_base": "yes" if is_base else "no",
            "description": entry.description,
            "version": "",
        })

    return result


def get_active_voices_bin() -> Path:
    """Return the voices.bin path that koko should use.

    If user has a custom voices.bin, return that. Otherwise system path.
    """
    return _active_voices_bin()


def download_voice(voice_id: str) -> tuple[bool, str]:
    """Download a voice from HuggingFace and add to user voices.bin.

    Returns (success, error_message).
    """
    if voice_id not in _CATALOG_BY_ID:
        return False, f"Unknown voice: {voice_id}"

    if voice_id in BASE_VOICE_IDS and not USER_VOICES_BIN.exists():
        return True, ""  # Already in system voices.bin

    # Download .pt from HuggingFace
    url = f"{HF_BASE_URL}/{voice_id}.pt"
    try:
        logger.info("Downloading Kokoro voice %s from %s", voice_id, url)
        req = Request(url, headers={"User-Agent": "biglinux-tts/1.0"})  # noqa: S310
        with urlopen(req, timeout=60) as resp:  # noqa: S310
            pt_data = resp.read()
    except (URLError, OSError, TimeoutError) as e:
        return False, f"Download failed: {e}"

    # Convert .pt → .npy
    try:
        npy_data = _pt_to_npy(pt_data, voice_id)
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        return False, f"Conversion failed: {e}"

    # Ensure user voices.bin exists (copy from system if needed)
    try:
        _ensure_user_voices_bin()
    except OSError as e:
        return False, f"Cannot create user voices directory: {e}"

    # Add to user voices.bin
    try:
        _add_voice_to_zip(voice_id, npy_data)
    except (zipfile.BadZipFile, OSError) as e:
        return False, f"Failed to add voice: {e}"

    logger.info("Voice %s installed successfully", voice_id)
    return True, ""


def remove_voice(voice_id: str) -> tuple[bool, str]:
    """Remove a voice from user voices.bin.

    Returns (success, error_message).
    Base voices cannot be removed.
    """
    if voice_id in BASE_VOICE_IDS:
        return False, "Cannot remove base voice"

    if not USER_VOICES_BIN.exists():
        return False, "No user voices installed"

    try:
        _remove_voice_from_zip(voice_id)
    except (zipfile.BadZipFile, OSError) as e:
        return False, f"Failed to remove voice: {e}"

    logger.info("Voice %s removed", voice_id)
    return True, ""


# ── Internal helpers ─────────────────────────────────────────────────

def _active_voices_bin() -> Path:
    """Return the voices.bin to use: user copy if exists, else system."""
    if USER_VOICES_BIN.exists():
        return USER_VOICES_BIN
    return SYSTEM_VOICES_BIN


def _ensure_user_voices_bin() -> None:
    """Create user voices dir and copy system voices.bin if needed."""
    USER_VOICES_DIR.mkdir(parents=True, exist_ok=True)
    if not USER_VOICES_BIN.exists():
        if SYSTEM_VOICES_BIN.exists():
            shutil.copy2(SYSTEM_VOICES_BIN, USER_VOICES_BIN)
        else:
            # Create empty ZIP
            with zipfile.ZipFile(USER_VOICES_BIN, "w"):
                pass


def _pt_to_npy(pt_data: bytes, voice_id: str) -> bytes:
    """Convert a PyTorch .pt voice file to NumPy .npy format.

    The .pt file is a ZIP containing a data/0 entry with raw float32 tensor data.
    The internal directory name varies per file, so we search for */data/0.
    """
    with zipfile.ZipFile(io.BytesIO(pt_data), "r") as z:
        # Find the raw tensor data entry (pattern: {name}/data/0)
        data_entry = None
        for name in z.namelist():
            if name.endswith("/data/0"):
                data_entry = name
                break
        if data_entry is None:
            raise KeyError(f"No tensor data entry found in {voice_id}.pt")
        raw = z.read(data_entry)

    expected_size = _VOICE_SHAPE[0] * _VOICE_SHAPE[1] * _VOICE_SHAPE[2] * np.dtype(_VOICE_DTYPE).itemsize
    if len(raw) != expected_size:
        raise ValueError(
            f"Unexpected data size for {voice_id}: {len(raw)} (expected {expected_size})"
        )

    arr = np.frombuffer(raw, dtype=_VOICE_DTYPE).reshape(_VOICE_SHAPE)
    buf = io.BytesIO()
    np.save(buf, arr)
    return buf.getvalue()


def _add_voice_to_zip(voice_id: str, npy_data: bytes) -> None:
    """Add or replace a voice .npy in user voices.bin."""
    npy_name = f"{voice_id}.npy"

    # Read existing, filter out the voice if already present, re-write
    entries: dict[str, bytes] = {}
    if USER_VOICES_BIN.exists():
        with zipfile.ZipFile(USER_VOICES_BIN, "r") as z:
            for name in z.namelist():
                entries[name] = z.read(name)

    entries[npy_name] = npy_data

    with zipfile.ZipFile(USER_VOICES_BIN, "w", zipfile.ZIP_STORED) as z:
        for name, data in sorted(entries.items()):
            z.writestr(name, data)


def _remove_voice_from_zip(voice_id: str) -> None:
    """Remove a voice .npy from user voices.bin.

    If only base voices remain after removal, delete user copy
    to fall back to the system voices.bin.
    """
    npy_name = f"{voice_id}.npy"

    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(USER_VOICES_BIN, "r") as z:
        for name in z.namelist():
            if name != npy_name:
                entries[name] = z.read(name)

    # Check if only base voices remain
    remaining_ids = {n.removesuffix(".npy") for n in entries if n.endswith(".npy")}
    if remaining_ids <= BASE_VOICE_IDS:
        # Only base voices — delete user copy, fall back to system
        USER_VOICES_BIN.unlink(missing_ok=True)
        return

    with zipfile.ZipFile(USER_VOICES_BIN, "w", zipfile.ZIP_STORED) as z:
        for name, data in sorted(entries.items()):
            z.writestr(name, data)
