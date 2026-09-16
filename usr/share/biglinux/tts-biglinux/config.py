"""
Configuration, constants, enums and dataclasses for BigLinux TTS v4.0.0
Single source of truth for all application settings and defaults.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Application Identity ──────────────────────────────────────────────

APP_ID = "br.com.biglinux.tts"
APP_NAME = "BigLinux TTS"
APP_VERSION = "4.0.0"

# Settings schema version — bump when the on-disk shape changes so future
# releases can migrate deterministically.
CONFIG_VERSION = 1
APP_DEVELOPERS = ["Tales A. Mendonça", "Bruno Gonçalves Araujo", "Rafael Ruscher"]
APP_WEBSITE = "https://www.biglinux.com.br"
APP_ISSUE_URL = "https://github.com/biglinux/tts-biglinux/issues"

# ── Paths ─────────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "biglinux-tts"
LEGACY_CONFIG_DIR = Path.home() / ".config" / "tts-biglinux"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
PID_FILE = Path("/tmp") / f"biglinux-tts-{Path.home().name}.pid"  # noqa: S108

# Icon paths
ICONS_DIR = Path("/usr/share/icons/hicolor/scalable/apps")
ICON_APP = ICONS_DIR / "tts-biglinux.svg"

# Locale
LOCALE_DIR = Path("/usr/share/locale")
DEV_LOCALE_DIR = Path(__file__).parent.parent.parent / "usr" / "share" / "locale"

# ── Window Defaults ───────────────────────────────────────────────────

WINDOW_WIDTH_DEFAULT = 900
WINDOW_HEIGHT_DEFAULT = 740
WINDOW_WIDTH_MIN = 360
WINDOW_HEIGHT_MIN = 480

# ── UI Spacing ────────────────────────────────────────────────────────

MARGIN_SMALL = 6
MARGIN_DEFAULT = 12
MARGIN_LARGE = 24
SPACING_SMALL = 6
SPACING_DEFAULT = 12
SPACING_LARGE = 18

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

MAX_CHARS_MIN = 0  # 0 = unlimited
MAX_CHARS_MAX = 1000000
MAX_CHARS_DEFAULT = 0  # Unlimited by default
MAX_CHARS_STEP = 1000


# ── Enums ─────────────────────────────────────────────────────────────


class TTSBackend(str, Enum):
    """Available TTS backends."""

    SPEECH_DISPATCHER = "speech-dispatcher"
    RHVOICE = "rhvoice"
    ESPEAK_NG = "espeak-ng"
    PIPER = "piper"
    KOKORO = "kokoro"


class SpeakAction(str, Enum):
    """Action when already speaking and new text requested."""

    STOP_AND_SPEAK = "stop-and-speak"
    STOP = "stop"
    QUEUE = "queue"


class TTSState(str, Enum):
    """TTS engine state machine."""

    IDLE = "idle"
    SPEAKING = "speaking"
    ERROR = "error"


# ── Dataclasses ───────────────────────────────────────────────────────


# ── Kokoro-specific defaults ───────────────────────────────────────────

KOKORO_SPEED_MIN = 0.5
KOKORO_SPEED_MAX = 2.0
KOKORO_SPEED_DEFAULT = 1.0
KOKORO_SPEED_STEP = 0.05


@dataclass
class KokoroConfig:
    """Kokoro TTS specific parameters."""

    speed: float = KOKORO_SPEED_DEFAULT
    voice_blend: str = ""  # e.g. "af_heart,af_bella" for mixing
    blend_ratio: float = 0.5  # 0.0-1.0 ratio for voice blending
    emotion_preset: str = "neutral"  # neutral, happy, calm, urgent, narrative
    lang_code: str = "p"  # p=pt-br, a=en-us, b=en-gb, e=es, etc.


@dataclass
class HistoryConfig:
    """History save configuration."""

    enabled: bool = False
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
    output_module: str = ""
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
    enabled: bool = True
    show_in_launcher: bool = True  # Tray enabled by default


@dataclass
class WindowConfig:
    """Window geometry state."""

    width: int = WINDOW_WIDTH_DEFAULT
    height: int = WINDOW_HEIGHT_DEFAULT
    maximized: bool = False
    tray_warning_shown: bool = False


@dataclass
class AppSettings:
    """Complete application settings — single source of truth."""

    speech: SpeechConfig = field(default_factory=SpeechConfig)
    text: TextConfig = field(default_factory=TextConfig)
    shortcut: ShortcutConfig = field(default_factory=ShortcutConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    show_welcome: bool = True
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
    """Save settings to disk as JSON."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(settings)
    SETTINGS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
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
            speed=_safe_float(kokoro_data, "speed", KOKORO_SPEED_DEFAULT),
            voice_blend=_safe_str(kokoro_data, "voice_blend", ""),
            blend_ratio=_safe_float(kokoro_data, "blend_ratio", 0.5),
            emotion_preset=_safe_str(kokoro_data, "emotion_preset", "neutral"),
            lang_code=_safe_str(kokoro_data, "lang_code", "p"),
        )
        settings.speech = SpeechConfig(
            rate=_safe_int(s, "rate", RATE_DEFAULT),
            pitch=_safe_int(s, "pitch", PITCH_DEFAULT),
            volume=_safe_int(s, "volume", VOLUME_DEFAULT),
            voice_id=_safe_str(s, "voice_id", ""),
            backend=_safe_str(s, "backend", TTSBackend.RHVOICE.value),
            output_module=_safe_str(s, "output_module", "rhvoice"),
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
            enabled=_safe_bool(sc, "enabled", True),
            show_in_launcher=_safe_bool(sc, "show_in_launcher", True),
        )

    if _section("window"):
        w = _section("window")
        settings.window = WindowConfig(
            width=_safe_int(w, "width", WINDOW_WIDTH_DEFAULT),
            height=_safe_int(w, "height", WINDOW_HEIGHT_DEFAULT),
            maximized=_safe_bool(w, "maximized", False),
            tray_warning_shown=_safe_bool(w, "tray_warning_shown", False),
        )

    if _section("history"):
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

    return settings


def _migrate_legacy_settings() -> AppSettings:
    """Migrate from legacy ~/.config/tts-biglinux/ format."""
    settings = AppSettings()

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
    settings.speech.backend = TTSBackend.SPEECH_DISPATCHER.value
    settings.speech.output_module = "rhvoice"

    logger.info(
        "Migrated legacy settings: rate=%s pitch=%s voice=%s", rate, pitch, voice
    )
    return settings
