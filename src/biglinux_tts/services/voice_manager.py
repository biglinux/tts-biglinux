"""
Voice manager — discover, classify and manage installed TTS voices.

Scans for voices from all installed backends (speech-dispatcher/RHVoice,
espeak-ng, Piper) and provides a unified voice catalog.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from biglinux_tts.config import TTSBackend

logger = logging.getLogger(__name__)

# ── Voice Metadata ───────────────────────────────────────────────────


@dataclass
class VoiceInfo:
    """Metadata for a single TTS voice."""

    voice_id: str
    name: str
    language: str
    language_name: str
    backend: str
    output_module: str = ""
    gender: str = ""  # male, female, neutral
    quality: str = "standard"  # standard, neural, high
    description: str = ""


@dataclass
class VoiceCatalog:
    """Complete catalog of available voices, grouped by language."""

    voices: list[VoiceInfo] = field(default_factory=list)
    backends_available: list[str] = field(default_factory=list)

    def get_by_language(self, lang_code: str) -> list[VoiceInfo]:
        """Get voices matching a language code prefix (e.g. 'pt' matches 'pt-BR')."""
        return [v for v in self.voices if v.language.startswith(lang_code)]

    def get_by_backend(self, backend: str) -> list[VoiceInfo]:
        """Get voices from a specific backend."""
        return [v for v in self.voices if v.backend == backend]

    def find_voice(self, voice_id: str) -> VoiceInfo | None:
        """Find a voice by ID."""
        for v in self.voices:
            if v.voice_id == voice_id:
                return v
        return None

    def get_languages(self) -> list[tuple[str, str]]:
        """Get list of (code, name) for all available languages, sorted."""
        seen: dict[str, str] = {}
        for v in self.voices:
            code = v.language[:2]
            if code not in seen:
                seen[code] = v.language_name
        return sorted(seen.items(), key=lambda x: x[1])


# ── Language Name Mapping ────────────────────────────────────────────

LANGUAGE_NAMES: dict[str, str] = {
    "af": "Afrikaans",
    "am": "Amharic",
    "an": "Aragonese",
    "ar": "Arabic",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "bs": "Bosnian",
    "ca": "Catalan",
    "cmn": "Chinese (Mandarin)",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "eo": "Esperanto",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "ga": "Irish",
    "gd": "Scottish Gaelic",
    "gl": "Galician",
    "gu": "Gujarati",
    "hak": "Hakka Chinese",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "jbo": "Lojban",
    "ka": "Georgian",
    "kk": "Kazakh",
    "kl": "Greenlandic",
    "kn": "Kannada",
    "ko": "Korean",
    "ku": "Kurdish",
    "ky": "Kyrgyz",
    "la": "Latin",
    "lfn": "Lingua Franca Nova",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mi": "Maori",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mr": "Marathi",
    "ms": "Malay",
    "mt": "Maltese",
    "my": "Burmese",
    "nb": "Norwegian Bokmål",
    "ne": "Nepali",
    "nl": "Dutch",
    "no": "Norwegian",
    "om": "Oromo",
    "or": "Oriya",
    "pa": "Punjabi",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sd": "Sindhi",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sq": "Albanian",
    "sr": "Serbian",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tk": "Turkmen",
    "tn": "Setswana",
    "tr": "Turkish",
    "tt": "Tatar",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "yue": "Cantonese",
    "zh": "Chinese",
}


def _lang_name(code: str) -> str:
    """Get human-readable language name from ISO code."""
    short = code[:2].lower() if len(code) >= 2 else code
    return LANGUAGE_NAMES.get(short, LANGUAGE_NAMES.get(code, code))


# ── Voice Discovery ──────────────────────────────────────────────────


def discover_voices() -> VoiceCatalog:
    """
    Discover all available TTS voices from all installed backends.

    Returns:
        VoiceCatalog with all discovered voices.
    """
    catalog = VoiceCatalog()

    # Discover speech-dispatcher voices (RHVoice, espeak-ng module, etc.)
    spd_voices = _discover_spd_voices()
    catalog.voices.extend(spd_voices)
    if spd_voices:
        catalog.backends_available.append(TTSBackend.SPEECH_DISPATCHER.value)

    # Discover standalone espeak-ng voices
    espeak_voices = _discover_espeak_voices()
    catalog.voices.extend(espeak_voices)
    if espeak_voices:
        catalog.backends_available.append(TTSBackend.ESPEAK_NG.value)

    # Discover Piper voices
    piper_voices = _discover_piper_voices()
    catalog.voices.extend(piper_voices)
    if piper_voices:
        catalog.backends_available.append(TTSBackend.PIPER.value)

    logger.info(
        "Discovered %d voices from %d backends",
        len(catalog.voices),
        len(catalog.backends_available),
    )

    return catalog


def _discover_spd_voices() -> list[VoiceInfo]:
    """
    Discover voices available via speech-dispatcher.

    Uses spd-say -L for each known output module (rhvoice, espeak-ng),
    and also scans for installed RHVoice voice packages.
    """
    voices: list[VoiceInfo] = []

    # 1. Scan for installed RHVoice voice packages
    rhvoice_voices = _discover_rhvoice_installed()
    voices.extend(rhvoice_voices)

    # 2. Try spd-say -L for default module to find extra voices
    #    Skip voices already found via RHVoice scan (case/accent insensitive)
    import unicodedata

    def _normalize_id(s: str) -> str:
        s = unicodedata.normalize("NFD", s)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        return s.lower()

    known_ids = {_normalize_id(v.voice_id) for v in voices}

    try:
        proc = subprocess.run(
            ["spd-say", "-L"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            for line in proc.stdout.strip().splitlines():
                line = line.strip()
                if not line or "NAME" in line or "dummy" in line:
                    continue
                parts = re.split(r"\s{2,}", line)
                if len(parts) < 2:
                    continue
                voice_name = parts[0].strip()
                lang_code = parts[1].strip()
                if _normalize_id(voice_name) in known_ids:
                    continue
                voices.append(
                    VoiceInfo(
                        voice_id=voice_name,
                        name=voice_name.replace("-", " ").replace("_", " "),
                        language=lang_code,
                        language_name=_lang_name(lang_code),
                        backend=TTSBackend.SPEECH_DISPATCHER.value,
                        output_module="espeak-ng",
                        gender=_guess_gender(voice_name),
                        quality="standard",
                    )
                )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("spd-say not available")

    return voices


def _discover_rhvoice_installed() -> list[VoiceInfo]:
    """
    Discover RHVoice voices by scanning installed packages and voice directories.
    
    RHVoice voices on Arch are installed as packages like:
      rhvoice-voice-leticia-f123 (pt-BR female)
      rhvoice-voice-evgeniy-eng (en male)
    Voice data lives in /usr/share/RHVoice/voices/
    """
    voices: list[VoiceInfo] = []

    # Check if RHVoice is available in speech-dispatcher
    has_rhvoice = False
    try:
        proc = subprocess.run(
            ["spd-say", "-o", "rhvoice", "-L"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        has_rhvoice = proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if not has_rhvoice:
        # Fallback: check if rhvoice binary exists
        try:
            proc = subprocess.run(
                ["which", "RHVoice-test"],
                capture_output=True,
                timeout=3,
            )
            has_rhvoice = proc.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if not has_rhvoice:
        return voices

    # Scan for voice directories
    voice_dirs = [
        Path("/usr/share/RHVoice/voices"),
        Path("/usr/local/share/RHVoice/voices"),
    ]

    # Known RHVoice voice metadata (voice_dir_name → language, gender)
    known_voices: dict[str, tuple[str, str, str]] = {
        "Leticia-F123": ("pt-BR", "female", "Letícia F123"),
        "leticia-f123": ("pt-BR", "female", "Letícia F123"),
        "Evgeniy-Eng": ("en", "male", "Evgeniy"),
        "evgeniy-eng": ("en", "male", "Evgeniy"),
        "Natalia": ("ru", "female", "Natalia"),
        "Alan": ("en", "male", "Alan"),
        "Anna": ("ru", "female", "Anna"),
        "Elena": ("ru", "female", "Elena"),
        "Aleksandr": ("ru", "male", "Aleksandr"),
        "Artemiy": ("ru", "male", "Artemiy"),
        "Irina": ("ru", "female", "Irina"),
        "Lyubov": ("ru", "female", "Lyubov"),
        "Hana": ("cs", "female", "Hana"),
        "Volodymyr": ("uk", "male", "Volodymyr"),
        "Anatol": ("uk", "male", "Anatol"),
        "Natia": ("ka", "female", "Natia"),
        "Spomenka": ("hr", "female", "Spomenka"),
        "Kiko": ("mk", "male", "Kiko"),
        "Natan": ("pl", "male", "Natan"),
        "Magda": ("pl", "female", "Magda"),
        "Suze": ("nl", "female", "Suze"),
        "Azamat": ("tt", "male", "Azamat"),
        "Talgat": ("ky", "male", "Talgat"),
        "Zdenek": ("cs", "male", "Zdeněk"),
    }

    found_dirs: set[str] = set()
    for vdir in voice_dirs:
        if not vdir.exists():
            continue
        for entry in vdir.iterdir():
            if not entry.is_dir():
                continue
            dirname = entry.name
            if dirname in found_dirs:
                continue
            found_dirs.add(dirname)

            # Match against known voices
            meta = known_voices.get(dirname)
            if meta:
                lang, gender, display_name = meta
            else:
                # Guess from directory name
                lang = "en"
                gender = _guess_gender(dirname)
                display_name = dirname.replace("-", " ").replace("_", " ").title()

            voices.append(
                VoiceInfo(
                    voice_id=dirname,
                    name=display_name,
                    language=lang,
                    language_name=_lang_name(lang[:2]),
                    backend=TTSBackend.SPEECH_DISPATCHER.value,
                    output_module="rhvoice",
                    gender=gender,
                    quality="high",
                    description="RHVoice — high quality local synthesis",
                )
            )

    # If no directories found, try pacman query as fallback
    if not voices:
        voices = _discover_rhvoice_from_pacman()

    return voices


def _discover_rhvoice_from_pacman() -> list[VoiceInfo]:
    """Fallback: discover RHVoice voices from installed packages."""
    voices: list[VoiceInfo] = []
    try:
        proc = subprocess.run(
            ["pacman", "-Qq"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return voices

        for line in proc.stdout.strip().splitlines():
            if not line.startswith("rhvoice-voice-"):
                continue
            voice_pkg = line.strip()
            # Extract voice name from package name: rhvoice-voice-leticia-f123
            voice_name = voice_pkg.removeprefix("rhvoice-voice-")

            # Map known package names
            pkg_meta: dict[str, tuple[str, str, str]] = {
                "leticia-f123": ("pt-BR", "female", "Letícia F123"),
                "evgeniy-eng": ("en", "male", "Evgeniy"),
                "alan": ("en", "male", "Alan"),
                "natalia": ("ru", "female", "Natalia"),
                "anna": ("ru", "female", "Anna"),
                "elena": ("ru", "female", "Elena"),
            }

            meta = pkg_meta.get(voice_name)
            if meta:
                lang, gender, display = meta
            else:
                lang = "en"
                gender = _guess_gender(voice_name)
                display = voice_name.replace("-", " ").title()

            # Use the capitalized directory name as voice_id
            voice_id = voice_name.title().replace(" ", "-")
            voices.append(
                VoiceInfo(
                    voice_id=voice_id,
                    name=display,
                    language=lang,
                    language_name=_lang_name(lang[:2]),
                    backend=TTSBackend.SPEECH_DISPATCHER.value,
                    output_module="rhvoice",
                    gender=gender,
                    quality="high",
                    description="RHVoice — high quality local synthesis",
                )
            )

    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return voices


def _discover_espeak_voices() -> list[VoiceInfo]:
    """Discover voices via espeak-ng --voices."""
    voices: list[VoiceInfo] = []

    try:
        proc = subprocess.run(
            ["espeak-ng", "--voices"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return voices
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("espeak-ng not available")
        return voices

    # Format: "Pty Language  Age/Gender VoiceName  File  OtherLanguages"
    for line in proc.stdout.strip().splitlines()[1:]:  # Skip header
        parts = line.split()
        if len(parts) < 4:
            continue

        # parts[0] = priority, parts[1] = language, parts[2] = age/gender, parts[3] = name
        lang_code = parts[1]
        age_gender = parts[2]  # e.g. "--/M" or "--/F"
        voice_name = parts[3]

        gender = ""
        if "/M" in age_gender:
            gender = "male"
        elif "/F" in age_gender:
            gender = "female"

        # Use language code as voice_id (espeak-ng -v accepts lang codes)
        voice_id = f"espeak-{lang_code}"
        voices.append(
            VoiceInfo(
                voice_id=voice_id,
                name=voice_name.replace("-", " ").replace("_", " ").title(),
                language=lang_code,
                language_name=_lang_name(lang_code),
                backend=TTSBackend.ESPEAK_NG.value,
                gender=gender,
                quality="standard",
            )
        )

    return voices


def _discover_piper_voices() -> list[VoiceInfo]:
    """
    Discover Piper TTS voice models installed on the system.

    BigLinux packages install voices to:
      /usr/share/piper-voices/{lang}/{lang_REGION}/{speaker}/{quality}/{lang_REGION}-{speaker}-{quality}.onnx
    The binary is /usr/bin/piper-tts (package: piper-tts-bin).
    """
    voices: list[VoiceInfo] = []

    # Check if piper-tts binary exists
    piper_bin = _find_piper_binary()
    if not piper_bin:
        logger.debug("Piper TTS binary not found")
        return voices

    search_dirs = [
        Path("/usr/share/piper-voices"),
        Path("/usr/local/share/piper-voices"),
        Path.home() / ".local" / "share" / "piper-voices",
    ]

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue

        for onnx_file in search_dir.rglob("*.onnx"):
            # Skip if no config file alongside
            config_file = Path(str(onnx_file) + ".json")
            if not config_file.exists():
                continue

            # Parse path: .../pt/pt_BR/edresson/low/pt_BR-edresson-low.onnx
            # voice_id = absolute path to model for reliable lookup
            stem = onnx_file.stem  # pt_BR-edresson-low
            parts = stem.split("-")
            if len(parts) < 2:
                continue

            lang_region = parts[0]  # pt_BR
            speaker = parts[1] if len(parts) > 1 else "default"
            quality = parts[2] if len(parts) > 2 else "medium"

            # Normalize language code
            lang_code = lang_region.replace("_", "-")  # pt-BR
            lang_short = lang_code.split("-")[0]  # pt

            # Voice ID = path relative to search_dir for portability
            voice_id = f"piper:{onnx_file}"

            quality_label = {
                "x_low": "Extra Low",
                "low": "Low",
                "medium": "Medium",
                "high": "High",
            }.get(quality, quality.title())

            voices.append(
                VoiceInfo(
                    voice_id=voice_id,
                    name=f"{speaker.title()} ({quality_label})",
                    language=lang_code,
                    language_name=_lang_name(lang_short),
                    backend=TTSBackend.PIPER.value,
                    quality="neural",
                    gender=_guess_gender(speaker),
                    description=f"Piper Neural TTS — {lang_code} {quality_label}",
                )
            )

    logger.info("Piper: found %d voice models", len(voices))
    return voices


def _find_piper_binary() -> str | None:
    """Find the piper TTS binary on the system."""
    candidates = ["piper-tts", "piper"]
    for name in candidates:
        try:
            proc = subprocess.run(
                ["which", name],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Check known install locations
    for path in ["/usr/bin/piper-tts", "/opt/piper-tts/piper"]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return None


def _guess_gender(name: str) -> str:
    """Best-effort gender guess from voice name."""
    name_lower = name.lower()
    female_names = {
        "letícia", "leticia", "natalia", "anna", "elena", "irina",
        "lyubov", "marianna", "hana", "suze", "magda", "clb", "slt",
        "spomenka", "natia",
    }
    male_names = {
        "antonio", "evgeniy", "alan", "bdl", "aleksandr", "artemiy",
        "anatol", "volodymyr", "zdenek", "natan", "kiko", "azamat",
        "talgat",
    }

    for fn in female_names:
        if fn in name_lower:
            return "female"
    for mn in male_names:
        if mn in name_lower:
            return "male"
    return ""


def get_default_voice_for_language(catalog: VoiceCatalog, lang: str) -> str:
    """Get the best default voice for a language."""
    lang_voices = catalog.get_by_language(lang)
    if not lang_voices:
        # Try English as fallback
        lang_voices = catalog.get_by_language("en")
    if not lang_voices:
        return ""

    # Prefer: neural > high > standard
    quality_order = {"neural": 0, "high": 1, "standard": 2}
    lang_voices.sort(key=lambda v: quality_order.get(v.quality, 99))

    return lang_voices[0].voice_id
