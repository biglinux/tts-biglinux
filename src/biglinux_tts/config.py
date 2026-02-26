"""
Configuration, constants, enums and dataclasses for BigLinux TTS.

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
APP_VERSION = "1.0.0"
APP_DEVELOPER = "BigLinux Team"
APP_WEBSITE = "https://www.biglinux.com.br"
APP_ISSUE_URL = "https://github.com/biglinux/tts-biglinux/issues"

# ── Paths ─────────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "biglinux-tts"
LEGACY_CONFIG_DIR = Path.home() / ".config" / "tts-biglinux"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
PID_FILE = Path("/tmp") / f"biglinux-tts-{Path.home().name}.pid"  # noqa: S108

# Icon paths
ICONS_DIR = Path("/usr/share/icons/hicolor/scalable/apps")
ICON_APP = ICONS_DIR / "biglinux-tts.svg"

# Locale
LOCALE_DIR = Path("/usr/share/locale")
DEV_LOCALE_DIR = Path(__file__).parent.parent.parent / "usr" / "share" / "locale"

# ── Window Defaults ───────────────────────────────────────────────────

WINDOW_WIDTH_DEFAULT = 560
WINDOW_HEIGHT_DEFAULT = 680
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

MAX_CHARS_MIN = 0           # 0 = unlimited
MAX_CHARS_MAX = 1000000
MAX_CHARS_DEFAULT = 0       # Unlimited by default
MAX_CHARS_STEP = 1000


# ── Enums ─────────────────────────────────────────────────────────────


class TTSBackend(str, Enum):
    """Available TTS backends."""

    SPEECH_DISPATCHER = "speech-dispatcher"
    ESPEAK_NG = "espeak-ng"
    PIPER = "piper"


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


@dataclass
class SpeechConfig:
    """Voice and speech parameters."""

    rate: int = RATE_DEFAULT
    pitch: int = PITCH_DEFAULT
    volume: int = VOLUME_DEFAULT
    voice_id: str = ""
    backend: str = TTSBackend.SPEECH_DISPATCHER.value
    output_module: str = "rhvoice"


@dataclass
class TextConfig:
    """Text processing options."""

    expand_abbreviations: bool = True
    process_urls: bool = False
    process_special_chars: bool = True
    strip_formatting: bool = True
    max_chars: int = MAX_CHARS_DEFAULT


@dataclass
class ShortcutConfig:
    """Keyboard shortcut configuration."""

    keybinding: str = "<Alt>v"
    enabled: bool = True
    show_in_launcher: bool = False


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


# ── Settings Persistence ──────────────────────────────────────────────


def load_settings() -> AppSettings:
    """Load settings from disk, with legacy migration and defaults."""
    settings = AppSettings()

    # Try loading current settings
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            settings = _deserialize_settings(data)
            logger.debug("Settings loaded from %s", SETTINGS_FILE)
            return settings
        except (json.JSONDecodeError, KeyError, TypeError):
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


def _deserialize_settings(data: dict) -> AppSettings:
    """Deserialize settings dict to AppSettings dataclass."""
    settings = AppSettings()

    if "speech" in data:
        s = data["speech"]
        settings.speech = SpeechConfig(
            rate=int(s.get("rate", RATE_DEFAULT)),
            pitch=int(s.get("pitch", PITCH_DEFAULT)),
            volume=int(s.get("volume", VOLUME_DEFAULT)),
            voice_id=str(s.get("voice_id", "")),
            backend=str(s.get("backend", TTSBackend.SPEECH_DISPATCHER.value)),
            output_module=str(s.get("output_module", "rhvoice")),
        )

    if "text" in data:
        t = data["text"]
        settings.text = TextConfig(
            expand_abbreviations=bool(t.get("expand_abbreviations", True)),
            process_urls=bool(t.get("process_urls", False)),
            process_special_chars=bool(t.get("process_special_chars", True)),
            strip_formatting=bool(t.get("strip_formatting", True)),
            max_chars=int(t.get("max_chars", MAX_CHARS_DEFAULT)),
        )

    if "shortcut" in data:
        sc = data["shortcut"]
        settings.shortcut = ShortcutConfig(
            keybinding=str(sc.get("keybinding", "<Alt>v")),
            enabled=bool(sc.get("enabled", True)),
        )

    if "window" in data:
        w = data["window"]
        settings.window = WindowConfig(
            width=int(w.get("width", WINDOW_WIDTH_DEFAULT)),
            height=int(w.get("height", WINDOW_HEIGHT_DEFAULT)),
            maximized=bool(w.get("maximized", False)),
        )

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

    logger.info("Migrated legacy settings: rate=%s pitch=%s voice=%s", rate, pitch, voice)
    return settings
