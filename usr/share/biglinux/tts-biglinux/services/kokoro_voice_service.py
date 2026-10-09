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
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import NamedTuple
from urllib.request import Request, urlopen
from urllib.error import URLError

from utils.i18n import _

logger = logging.getLogger(__name__)

# Voice ids are safe identifiers only — never allow path separators / traversal
# into a download URL or a ZIP entry name.
_RE_SAFE_VOICE_ID = re.compile(r"^[A-Za-z0-9_]+$")

# ── Paths ────────────────────────────────────────────────────────────

SYSTEM_VOICES_BIN = Path("/usr/share/biglinux-kokoro-tts/voices/voices.bin")
USER_VOICES_DIR = Path.home() / ".local" / "share" / "biglinux-tts" / "kokoro-voices"
USER_VOICES_BIN = USER_VOICES_DIR / "voices.bin"

HF_REPO = "hexgrad/Kokoro-82M"
HF_BASE_URL = f"https://huggingface.co/{HF_REPO}/resolve/main/voices"

# Shape of each Kokoro voice style vector (float32). numpy is imported only
# when a voice is converted: it is not needed to start the app or to speak.
_VOICE_SHAPE = (510, 1, 256)
_VOICE_ITEMSIZE = 4


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
    """Whether the koko engine (package biglinux-kokoro-tts) is installed."""
    return shutil.which("koko") is not None


# ── koko command line (shared by playback and the Voice Manager preview) ──
#
# Both paths MUST build the koko invocation here so that what the preview
# plays is exactly what the shortcut plays: same model, same voices.bin, same
# language, and a writable output location.

SYSTEM_MODEL = Path("/usr/share/biglinux-kokoro-tts/model/model.onnx")

# koko takes an espeak language id; Kokoro voice ids start with a language
# letter (pf_/pm_ = Brazilian Portuguese, af_/am_ = US English, ...).
_KOKO_LANG_BY_PREFIX = {
    "a": "en-us", "b": "en-gb", "p": "pt-br", "e": "es", "f": "fr",
    "i": "it", "h": "hi", "j": "ja", "z": "zh",
}
DEFAULT_KOKORO_VOICE = "pf_dora"

_koko_workdir_cache: str | None = None


def kokoro_model_path() -> Path:
    """The Kokoro ONNX model koko should load (explicit, never koko's default).

    Without ``-m`` koko falls back to a model path relative to its cwd and
    tries to download it there.
    """
    env = os.environ.get("KOKO_MODEL_PATH", "")
    if env and os.path.isfile(env):
        return Path(env)
    return SYSTEM_MODEL


def koko_voice_name(voice_id: str) -> str:
    """``kokoro:pm_alex`` → ``pm_alex`` (default voice when empty)."""
    name = voice_id.removeprefix("kokoro:") if voice_id else ""
    return name or DEFAULT_KOKORO_VOICE


def koko_language(voice_id: str) -> str:
    """espeak language id koko should phonemize with, from the voice prefix."""
    return _KOKO_LANG_BY_PREFIX.get(koko_voice_name(voice_id)[:1], "en-us")


def koko_workdir() -> str:
    """A private, writable working directory for the koko binary.

    koko writes its output WAV (``tmp/pipe_output.wav`` by default) relative to
    its current directory. Inheriting the app's cwd — the root-owned install
    dir under /usr/share — makes it exit with "Permission denied" and nothing
    is heard.
    """
    global _koko_workdir_cache
    if _koko_workdir_cache and os.access(_koko_workdir_cache, os.W_OK):
        return _koko_workdir_cache
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    path = ""
    if runtime and os.path.isdir(runtime):
        candidate = os.path.join(runtime, "biglinux-tts", "koko")
        try:
            os.makedirs(candidate, mode=0o700, exist_ok=True)
            if os.access(candidate, os.W_OK):
                path = candidate
        except OSError:
            pass
    if not path:
        path = tempfile.mkdtemp(prefix="biglinux-tts-koko-")
    _koko_workdir_cache = path
    return path


# Expression presets change the speaking speed (both Kokoro backends).
KOKORO_EMOTION_SPEED = {
    "neutral": 1.0,
    "happy": 1.1,
    "calm": 0.8,
    "urgent": 1.4,
    "narrative": 0.9,
}


def kokoro_speed(rate: int, emotion: str = "neutral") -> float:
    """UI rate (-100..100) and expression preset → Kokoro speed (0.5..2.0)."""
    base = max(0.5, min(2.0, 1.0 + (rate / 100.0)))
    return max(0.5, min(2.0, base * KOKORO_EMOTION_SPEED.get(emotion, 1.0)))


def koko_style(voice_id: str, blend: str = "", blend_ratio: float = 0.5) -> str:
    """koko ``-s`` value: one voice, or ``a.W+b.W`` (weights 1..9 of 10)."""
    voice = koko_voice_name(voice_id)
    other = blend.removeprefix("kokoro:") if blend else ""
    if not other or other == voice or not _RE_SAFE_VOICE_ID.match(other):
        return voice
    second = max(1, min(9, round(blend_ratio * 10)))
    return f"{voice}.{10 - second}+{other}.{second}"


def koko_problem(voice_id: str, blend: str = "") -> str:
    """Why koko cannot speak with ``voice_id`` right now ("" when it can).

    Returned text is translated and tells the person how to fix it. koko
    itself silently substitutes another voice for an unknown one, so the voice
    is checked against voices.bin here.
    """
    if shutil.which("koko") is None:
        return _("Kokoro is not installed. Install the biglinux-kokoro-tts package.")
    if not kokoro_model_path().is_file():
        return _("The Kokoro voice model is missing. Reinstall the biglinux-kokoro-tts package.")
    if not get_active_voices_bin().is_file():
        return _("No Kokoro voices were found. Open the Voice Manager to download a voice.")
    installed = get_installed_voice_ids()
    wanted = [koko_voice_name(voice_id)] + ([blend.removeprefix("kokoro:")] if blend else [])
    if installed and any(v not in installed for v in wanted):
        return _("This Kokoro voice is not installed. Open the Voice Manager to download it.")
    return ""


def build_koko_command(
    voice_id: str,
    *,
    speed: float = 1.0,
    text: str | None = None,
    output: str | None = None,
    koko_path: str | None = None,
    blend: str = "",
    blend_ratio: float = 0.5,
) -> list[str]:
    """argv for koko.

    With ``text`` it renders that text to ``output`` (``koko text``). Without
    it, ``koko pipe`` reads stdin one line at a time and plays each line as
    soon as it is synthesized, writing everything it plays to ``output`` (a
    WAV file growing as it goes; default: the scratch file in koko_workdir).
    ``koko stream`` is not used: it mixes log lines into the audio on stdout.
    """
    voice = koko_voice_name(voice_id)
    cmd = [
        koko_path or shutil.which("koko") or "koko",
        "-m", str(kokoro_model_path()),
        "-d", str(get_active_voices_bin()),
        "-l", koko_language(voice),
        "-s", koko_style(voice, blend, blend_ratio),
        "--force-style", "true",
        "-p", f"{max(0.5, min(2.0, speed)):.2f}",
    ]
    if text is None:
        cmd += ["pipe", "-o", output or os.path.join(koko_workdir(), "pipe_output.wav")]
    else:
        if not output:
            raise ValueError("koko text needs an output path")
        cmd += ["text", "-o", output, "--", text]
    return cmd


# koko pipe prints this to stderr when a sentence's audio starts playing.
KOKO_AUDIO_STARTED_MARKER = "Streaming audio"

# The Kokoro model accepts at most 510 phoneme tokens per segment. `koko pipe`
# splits the input into sentences but does not limit a sentence's length: a
# long sentence panics ("index out of bounds: the len is 511 but the index is
# 550"). Portuguese yields roughly one token per character, so sentences are
# kept well below the limit.
KOKO_MAX_SEGMENT_CHARS = 220

# A voice .pt is ~0.5 MB; anything far larger is not a voice.
_MAX_VOICE_DOWNLOAD = 16 * 1024 * 1024

_RE_DOTS = re.compile(r"\.{2,}")
_RE_PUNCT_RUN = re.compile(r"[!?…]{2,}")
_RE_SENTENCE_END = re.compile(r"(?<=[.!?…;:])\s+")


def _split_long(sentence: str, limit: int) -> list[str]:
    """Split one sentence into pieces of at most ``limit`` characters.

    Commas first (natural pauses), then word boundaries; a single word longer
    than the limit is cut as a last resort.
    """
    if len(sentence) <= limit:
        return [sentence]
    pieces: list[str] = []
    current = ""
    for part in re.split(r"(?<=,)\s+", sentence):
        candidate = f"{current} {part}".strip()
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            pieces.append(current)
        if len(part) <= limit:
            current = part
            continue
        current = ""
        for word in part.split():
            while len(word) > limit:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.append(word[:limit])
                word = word[limit:]
            candidate = f"{current} {word}".strip()
            if len(candidate) <= limit:
                current = candidate
            else:
                pieces.append(current)
                current = word
    if current:
        pieces.append(current)
    return pieces


def prepare_koko_text(text: str, limit: int = KOKO_MAX_SEGMENT_CHARS) -> str:
    """Text ``koko pipe`` can read to the end: one sentence per line.

    - ASCII ellipses ("..." / "....") become "…", and runs of sentence
      punctuation ("?!", "!!!", "?…") become one sign: koko cuts a sentence at
      each sign and exits with ``SendError`` on the punctuation-only piece
      left in between ("Wait... this", "Really?! No").
    - Sentences longer than ``limit`` are split into shorter ones (see
      KOKO_MAX_SEGMENT_CHARS); each piece ends with punctuation so koko
      treats it as its own segment.
    - Segments without any letter or digit (only punctuation) are dropped.
    """
    text = _RE_DOTS.sub("…", text)
    text = _RE_PUNCT_RUN.sub(
        lambda m: "?" if "?" in m.group() else ("!" if "!" in m.group() else "…"), text
    )
    lines: list[str] = []
    for paragraph in text.splitlines():
        for sentence in _RE_SENTENCE_END.split(paragraph.strip()):
            sentence = " ".join(sentence.split())
            if not any(ch.isalnum() for ch in sentence):
                continue
            for piece in _split_long(sentence, limit):
                piece = piece.strip().rstrip(",")
                if not any(ch.isalnum() for ch in piece):
                    continue
                if piece[-1] not in ".!?…;:":
                    piece += "."
                lines.append(piece)
    return "\n".join(lines)


def get_installed_voice_ids() -> set[str]:
    """IDs of the voices present in the active voices.bin."""
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


def download_voice(
    voice_id: str,
    progress_cb=None,
    cancel_check=None,
) -> tuple[bool, str]:
    """Download a voice from HuggingFace and add to user voices.bin.

    Args:
        voice_id: catalog voice id.
        progress_cb: optional callable(downloaded_bytes, total_bytes) for a
            real progress bar (total may be 0 if the server omits Content-Length).
        cancel_check: optional callable() -> bool; when it returns True the
            download aborts cleanly.

    Returns (success, error_message).
    """
    if voice_id not in _CATALOG_BY_ID:
        return False, _("Unknown voice: {voice_id}").format(voice_id=voice_id)

    # Defense-in-depth: never interpolate an unsafe id into a URL or ZIP name,
    # even though the catalog whitelist already gates this.
    if not _RE_SAFE_VOICE_ID.match(voice_id):
        return False, _("Invalid voice id: {voice_id}").format(voice_id=repr(voice_id))

    if voice_id in BASE_VOICE_IDS and not USER_VOICES_BIN.exists():
        return True, ""  # Already in system voices.bin

    # Download .pt from HuggingFace, in chunks (real progress), with retries.
    url = f"{HF_BASE_URL}/{voice_id}.pt"
    pt_data = b""
    last_err = ""
    for attempt in range(3):
        if cancel_check and cancel_check():
            return False, "cancelled"
        try:
            logger.info("Downloading Kokoro voice %s (attempt %d)", voice_id, attempt + 1)
            req = Request(url, headers={"User-Agent": "biglinux-tts/1.0"})  # noqa: S310
            with urlopen(req, timeout=60) as resp:  # noqa: S310
                try:
                    total = int(resp.headers.get("Content-Length", 0) or 0)
                except (TypeError, ValueError):
                    total = 0
                if total > _MAX_VOICE_DOWNLOAD:
                    return False, _("The voice file is larger than expected.")
                buf = bytearray()
                cancelled = False
                while True:
                    if cancel_check and cancel_check():
                        cancelled = True
                        break
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > _MAX_VOICE_DOWNLOAD:
                        return False, _("The voice file is larger than expected.")
                    if progress_cb:
                        try:
                            progress_cb(len(buf), total)
                        except Exception:
                            pass
                if cancelled:
                    return False, "cancelled"
                if total and len(buf) != total:
                    # Connection dropped mid-file: never convert a partial file.
                    last_err = _("incomplete download ({received} of {total} bytes)").format(received=len(buf), total=total)
                    buf = bytearray()
                pt_data = bytes(buf)
            if pt_data:
                break
            last_err = last_err or _("empty response")
        except (URLError, OSError, TimeoutError) as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))  # backoff
    if not pt_data:
        return False, _("Could not download the voice. Check your internet connection and try again. ({error})").format(error=last_err)

    # Convert .pt → .npy (validates shape and values: a corrupted download
    # never reaches voices.bin)
    try:
        npy_data = _pt_to_npy(pt_data, voice_id)
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        return False, _("The downloaded voice file is damaged. Try again. ({error})").format(error=e)

    # voices.bin is rewritten atomically: room for a full copy is needed.
    current = _active_voices_bin()
    needed = (current.stat().st_size if current.exists() else 0) + len(npy_data) + 1_048_576
    try:
        USER_VOICES_DIR.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(USER_VOICES_DIR).free
    except OSError:
        free = needed
    if free < needed:
        return False, _("Not enough disk space to install the voice ({size} MB needed).").format(
            size=max(1, needed // 1_048_576)
        )

    # Ensure user voices.bin exists (copy from system if needed)
    try:
        _ensure_user_voices_bin()
    except OSError as e:
        return False, _("Cannot create user voices directory: {error}").format(error=e)

    # Add to user voices.bin
    try:
        _add_voice_to_zip(voice_id, npy_data)
    except (zipfile.BadZipFile, OSError) as e:
        return False, _("Failed to add voice: {error}").format(error=e)

    logger.info("Voice %s installed successfully", voice_id)
    return True, ""


def remove_voice(voice_id: str) -> tuple[bool, str]:
    """Remove a voice from user voices.bin.

    Returns (success, error_message).
    Base voices cannot be removed.
    """
    if voice_id in BASE_VOICE_IDS:
        return False, _("Cannot remove base voice")

    if not USER_VOICES_BIN.exists():
        return False, _("No user voices installed")

    try:
        _remove_voice_from_zip(voice_id)
    except (zipfile.BadZipFile, OSError) as e:
        return False, _("Failed to remove voice: {error}").format(error=e)

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
    import numpy as np

    with zipfile.ZipFile(io.BytesIO(pt_data), "r") as z:
        # Find the raw tensor data entry (pattern: {name}/data/0)
        data_entry = None
        for name in z.namelist():
            if name.endswith("/data/0"):
                data_entry = name
                break
        if data_entry is None:
            raise KeyError(f"No tensor data entry found in {voice_id}.pt")
        expected_size = _VOICE_SHAPE[0] * _VOICE_SHAPE[1] * _VOICE_SHAPE[2] * _VOICE_ITEMSIZE
        # Checked before decompressing: a bad archive never fills the memory.
        if z.getinfo(data_entry).file_size != expected_size:
            raise ValueError(f"Unexpected data size for {voice_id}")
        raw = z.read(data_entry)

    if len(raw) != expected_size:
        raise ValueError(
            f"Unexpected data size for {voice_id}: {len(raw)} (expected {expected_size})"
        )

    arr = np.frombuffer(raw, dtype=np.float32).reshape(_VOICE_SHAPE)
    if not np.isfinite(arr).all():
        raise ValueError(f"{voice_id} contains invalid values")
    buf = io.BytesIO()
    np.save(buf, arr)
    return buf.getvalue()


def _write_voices_bin_atomic(entries: dict[str, bytes]) -> None:
    """Write voices.bin atomically: temp file in same dir → os.replace.

    Guarantees the user's existing voices.bin is never left truncated/corrupt
    if the process is interrupted mid-write (the previous in-place rewrite could
    destroy ALL installed voices).
    """
    USER_VOICES_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(USER_VOICES_DIR), suffix=".bin.part")
    try:
        with os.fdopen(fd, "wb") as f:
            with zipfile.ZipFile(f, "w", zipfile.ZIP_STORED) as z:
                for name, data in sorted(entries.items()):
                    z.writestr(name, data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(USER_VOICES_BIN))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _add_voice_to_zip(voice_id: str, npy_data: bytes) -> None:
    """Add or replace a voice .npy in user voices.bin (atomic)."""
    npy_name = f"{voice_id}.npy"

    entries: dict[str, bytes] = {}
    if USER_VOICES_BIN.exists():
        with zipfile.ZipFile(USER_VOICES_BIN, "r") as z:
            for name in z.namelist():
                entries[name] = z.read(name)

    entries[npy_name] = npy_data
    _write_voices_bin_atomic(entries)


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

    _write_voices_bin_atomic(entries)
