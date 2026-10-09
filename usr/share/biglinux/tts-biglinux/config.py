"""
Configuration, constants, enums and dataclasses for BigLinux TTS.
Single source of truth for all application settings and defaults.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Application Identity ──────────────────────────────────────────────

APP_ID = "br.com.biglinux.tts"
APP_NAME = "BigLinux TTS"
APP_VERSION = "4.1.0"

# Settings schema version, stored in settings.json: bump it (and migrate in
# _deserialize_settings) when the meaning of a stored value changes.
CONFIG_VERSION = 1
APP_DEVELOPERS = [
    "Rafael Ruscher <rruscher@gmail.com>",
    "Bruno Gonçalves <bigbruno@gmail.com>",
    "Tales A. Mendonça <talesam@gmail.com>",
]
APP_COPYRIGHT = "© 2021–2026 BigLinux contributors"
APP_WEBSITE = "https://github.com/biglinux/tts-biglinux"
APP_ISSUE_URL = "https://github.com/biglinux/tts-biglinux/issues"

# ── Paths ─────────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "biglinux-tts"
LEGACY_CONFIG_DIR = Path.home() / ".config" / "tts-biglinux"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

# ── Window Defaults ───────────────────────────────────────────────────

WINDOW_WIDTH_DEFAULT = 900
WINDOW_HEIGHT_DEFAULT = 740
WINDOW_WIDTH_MIN = 360
WINDOW_HEIGHT_MIN = 480

# ── TTS Parameter Ranges ─────────────────────────────────────────────

RATE_MIN = -100
RATE_MAX = 100
RATE_DEFAULT = -25
RATE_STEP = 5

PITCH_MIN = -100
PITCH_MAX = 100
PITCH_DEFAULT = -25
PITCH_STEP = 5

VOLUME_MIN = 0
VOLUME_MAX = 100
VOLUME_DEFAULT = 75
VOLUME_STEP = 5

MAX_CHARS_DEFAULT = 0  # 0 = unlimited


# ── Enums ─────────────────────────────────────────────────────────────


class TTSBackend(str, Enum):
    """Available TTS backends."""

    RHVOICE = "rhvoice"
    ESPEAK_NG = "espeak-ng"
    PIPER = "piper"
    KOKORO = "kokoro"


class TTSState(str, Enum):
    """TTS engine state machine."""

    IDLE = "idle"
    LOADING = "loading"  # request accepted; the voice is being prepared, no sound yet
    SPEAKING = "speaking"  # audio is playing
    ERROR = "error"  # the last request failed; TTSService.last_error says why


# ── Dataclasses ───────────────────────────────────────────────────────


@dataclass
class KokoroConfig:
    """Kokoro TTS specific parameters."""

    voice_blend: str = ""  # e.g. "af_heart,af_bella" for mixing
    blend_ratio: float = 0.5  # 0.0-1.0 ratio for voice blending
    emotion_preset: str = "neutral"  # neutral, happy, calm, urgent, narrative


@dataclass
class HistoryConfig:
    """History save configuration."""

    # On for new installations (the History explains where it is stored).
    # Settings saved by earlier versions keep their value: see
    # _deserialize_settings, which never turns it on by itself.
    enabled: bool = True
    save_audio: bool = True
    save_text: bool = True
    playback_mode: str = "interrupt"  # interrupt | queue | simultaneous
    # Retention (0 = unlimited). Enforced on save; never deletes silently
    # outside these limits.
    max_entries: int = 1000
    max_age_days: int = 0


@dataclass
class SpeechConfig:
    """Voice and speech parameters."""

    rate: int = RATE_DEFAULT
    pitch: int = PITCH_DEFAULT
    volume: int = VOLUME_DEFAULT
    voice_id: str = ""
    backend: str = TTSBackend.RHVOICE.value
    kokoro: KokoroConfig = field(default_factory=KokoroConfig)


@dataclass
class TextConfig:
    """Text processing options."""

    expand_abbreviations: bool = True
    process_urls: bool = False
    process_special_chars: bool = True
    strip_formatting: bool = True
    normalize_numbers: bool = True
    max_chars: int = MAX_CHARS_DEFAULT


@dataclass
class ShortcutConfig:
    """Keyboard shortcut configuration."""

    keybinding: str = "<Alt>v"
    show_in_launcher: bool = True  # Tray enabled by default


@dataclass
class WindowConfig:
    """Window geometry state."""

    width: int = WINDOW_WIDTH_DEFAULT
    height: int = WINDOW_HEIGHT_DEFAULT
    maximized: bool = False


@dataclass
class AppSettings:
    """Complete application settings — single source of truth."""

    speech: SpeechConfig = field(default_factory=SpeechConfig)
    text: TextConfig = field(default_factory=TextConfig)
    shortcut: ShortcutConfig = field(default_factory=ShortcutConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    show_welcome: bool = True
    # Expose an MPRIS media player (system taskbar mini-player) while reading.
    show_media_player: bool = True
    config_version: int = CONFIG_VERSION


# ── Settings Persistence ──────────────────────────────────────────────


def load_settings() -> AppSettings:
    """Load settings from disk, with legacy migration and defaults."""
    settings = AppSettings()

    # Try loading current settings
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise TypeError("settings root is not an object")
            settings = _deserialize_settings(data)
            logger.debug("Settings loaded from %s", SETTINGS_FILE)
            return settings
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
            logger.warning("Corrupt settings file, using defaults")

    # Try migrating legacy settings
    if LEGACY_CONFIG_DIR.exists():
        settings = _migrate_legacy_settings()
        save_settings(settings)
        logger.info("Legacy settings migrated to new format")

    return settings


def save_settings(settings: AppSettings) -> None:
    """Save settings to disk as JSON (atomically: a crash never truncates it)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SETTINGS_FILE)
    logger.debug("Settings saved to %s", SETTINGS_FILE)


def _safe_int(d: dict, key: str, default: int) -> int:
    """Coerce d[key] to int, falling back to default on any bad value."""
    try:
        return int(d.get(key, default))
    except (ValueError, TypeError):
        return default


def _safe_float(d: dict, key: str, default: float) -> float:
    try:
        return float(d.get(key, default))
    except (ValueError, TypeError):
        return default


def _safe_bool(d: dict, key: str, default: bool) -> bool:
    v = d.get(key, default)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    try:
        return bool(v)
    except (ValueError, TypeError):
        return default


def _safe_str(d: dict, key: str, default: str) -> str:
    v = d.get(key, default)
    return v if isinstance(v, str) else (str(v) if v is not None else default)


def _backend(value: str) -> str:
    """A known engine; anything else (e.g. the removed "speech-dispatcher") → RHVoice."""
    known = {b.value for b in TTSBackend}
    return value if value in known else TTSBackend.RHVOICE.value


def _deserialize_settings(data: dict) -> AppSettings:
    """Deserialize settings dict to AppSettings dataclass.

    Per-field coercion is fault-tolerant: a single corrupt/wrong-typed key
    falls back to its default instead of discarding ALL preferences.
    """
    settings = AppSettings()
    settings.config_version = _safe_int(data, "config_version", CONFIG_VERSION)

    def _section(name: str) -> dict:
        v = data.get(name)
        return v if isinstance(v, dict) else {}

    if isinstance(data.get("speech"), dict):
        s = data["speech"]
        kokoro_data = s.get("kokoro", {})
        if not isinstance(kokoro_data, dict):
            kokoro_data = {}
        kokoro_cfg = KokoroConfig(
            voice_blend=_safe_str(kokoro_data, "voice_blend", ""),
            blend_ratio=_safe_float(kokoro_data, "blend_ratio", 0.5),
            emotion_preset=_safe_str(kokoro_data, "emotion_preset", "neutral"),
        )
        settings.speech = SpeechConfig(
            rate=_safe_int(s, "rate", RATE_DEFAULT),
            pitch=_safe_int(s, "pitch", PITCH_DEFAULT),
            volume=_safe_int(s, "volume", VOLUME_DEFAULT),
            voice_id=_safe_str(s, "voice_id", ""),
            # 4.0 dropped the speech-dispatcher engine: its users get RHVoice.
            backend=_backend(_safe_str(s, "backend", TTSBackend.RHVOICE.value)),
            kokoro=kokoro_cfg,
        )

    if _section("text"):
        t = _section("text")
        settings.text = TextConfig(
            expand_abbreviations=_safe_bool(t, "expand_abbreviations", True),
            process_urls=_safe_bool(t, "process_urls", False),
            process_special_chars=_safe_bool(t, "process_special_chars", True),
            strip_formatting=_safe_bool(t, "strip_formatting", True),
            normalize_numbers=_safe_bool(t, "normalize_numbers", True),
            max_chars=_safe_int(t, "max_chars", MAX_CHARS_DEFAULT),
        )

    if _section("shortcut"):
        sc = _section("shortcut")
        settings.shortcut = ShortcutConfig(
            keybinding=_safe_str(sc, "keybinding", "<Alt>v"),
            show_in_launcher=_safe_bool(sc, "show_in_launcher", True),
        )

    if _section("window"):
        w = _section("window")
        settings.window = WindowConfig(
            width=_safe_int(w, "width", WINDOW_WIDTH_DEFAULT),
            height=_safe_int(w, "height", WINDOW_HEIGHT_DEFAULT),
            maximized=_safe_bool(w, "maximized", False),
        )

    # Always read, even when absent: a settings file without the key comes
    # from a version where history was off by default, so it stays off.
    h = _section("history")
    settings.history = HistoryConfig(
        enabled=_safe_bool(h, "enabled", False),
        save_audio=_safe_bool(h, "save_audio", True),
        save_text=_safe_bool(h, "save_text", True),
        playback_mode=_safe_str(h, "playback_mode", "interrupt"),
        max_entries=_safe_int(h, "max_entries", 1000),
        max_age_days=_safe_int(h, "max_age_days", 0),
    )

    settings.show_welcome = _safe_bool(data, "show_welcome", True)
    settings.show_media_player = _safe_bool(data, "show_media_player", True)

    return settings


def _migrate_legacy_settings() -> AppSettings:
    """Migrate from legacy ~/.config/tts-biglinux/ format."""
    settings = AppSettings()
    settings.history.enabled = False  # an existing user: never turned on silently

    def _read_legacy(filename: str, default: str) -> str:
        filepath = LEGACY_CONFIG_DIR / filename
        if filepath.exists():
            try:
                return filepath.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        return default

    rate = _read_legacy("rate", str(RATE_DEFAULT))
    pitch = _read_legacy("pitch", str(PITCH_DEFAULT))
    volume = _read_legacy("volume", str(VOLUME_DEFAULT))
    voice = _read_legacy("voice", "")

    try:
        settings.speech.rate = int(rate)
    except ValueError:
        settings.speech.rate = RATE_DEFAULT

    try:
        settings.speech.pitch = int(pitch)
    except ValueError:
        settings.speech.pitch = PITCH_DEFAULT

    try:
        settings.speech.volume = int(volume)
    except ValueError:
        settings.speech.volume = VOLUME_DEFAULT

    settings.speech.voice_id = voice
    settings.speech.backend = TTSBackend.RHVOICE.value

    logger.info(
        "Migrated legacy settings: rate=%s pitch=%s voice=%s", rate, pitch, voice
    )
    return settings
