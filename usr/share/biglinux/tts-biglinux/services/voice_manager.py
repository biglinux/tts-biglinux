"""
Voice manager — discover, classify and manage installed TTS voices.

Scans for voices from all installed backends (speech-dispatcher/RHVoice,
espeak-ng, Piper) and provides a unified voice catalog.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from config import TTSBackend
from services.kokoro_voice_service import get_active_voices_bin
from services.text_processor import get_system_language
from utils.i18n import _
from utils.speechd_utils import try_restart_speechd

logger = logging.getLogger(__name__)

# Global flag to avoid repeated hammering of a broken daemon in a single session
_is_speechd_broken = False

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


from concurrent.futures import ThreadPoolExecutor

def discover_voices() -> VoiceCatalog:
    """
    Discover all available TTS voices from all installed backends in parallel.

    Returns:
        VoiceCatalog with all discovered voices.
    """
    global _is_speechd_broken
    _is_speechd_broken = False  # Reset on each new full catalog refresh attempt
    catalog = VoiceCatalog()

    # Parallelize discovery across backends
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_spd = executor.submit(_discover_spd_voices, retrying=False)
        future_espeak = executor.submit(_discover_espeak_voices)
        future_piper = executor.submit(_discover_piper_voices)
        future_rhvoice = executor.submit(_discover_rhvoice_voices)
        future_kokoro = executor.submit(_discover_kokoro_voices)

        # 1. Gather speech-dispatcher voices
        try:
            spd_voices = future_spd.result()
            catalog.voices.extend(spd_voices)
            if spd_voices:
                catalog.backends_available.append(TTSBackend.SPEECH_DISPATCHER.value)
        except Exception as e:
            logger.error("Error in speech-dispatcher discovery: %s", e)

        # 2. Gather espeak-ng voices
        try:
            espeak_voices = future_espeak.result()
            catalog.voices.extend(espeak_voices)
            if espeak_voices:
                catalog.backends_available.append(TTSBackend.ESPEAK_NG.value)
        except Exception as e:
            logger.error("Error in espeak-ng discovery: %s", e)

        # 3. Gather Piper voices
        try:
            piper_voices = future_piper.result()
            catalog.voices.extend(piper_voices)
            if piper_voices:
                catalog.backends_available.append(TTSBackend.PIPER.value)
        except Exception as e:
            logger.error("Error in Piper discovery: %s", e)

        # 4. Gather Native RHVoice voices
        try:
            rhvoice_voices = future_rhvoice.result()
            catalog.voices.extend(rhvoice_voices)
            if rhvoice_voices:
                catalog.backends_available.append(TTSBackend.RHVOICE.value)
        except Exception as e:
            logger.error("Error in RHVoice discovery: %s", e)

        # 5. Gather Kokoro voices
        try:
            kokoro_voices = future_kokoro.result()
            catalog.voices.extend(kokoro_voices)
            if kokoro_voices:
                catalog.backends_available.append(TTSBackend.KOKORO.value)
        except Exception as e:
            logger.error("Error in Kokoro discovery: %s", e)

    logger.info(
        "Discovered %d voices from %d backends",
        len(catalog.voices),
        len(catalog.backends_available),
    )

    return catalog


def _discover_spd_voices(retrying: bool = False) -> list[VoiceInfo]:
    """Discover voices available via speech-dispatcher."""
    global _is_speechd_broken
    voices: list[VoiceInfo] = []

    if _is_speechd_broken:
        logger.debug("Skipping main spd discovery as daemon is marked broken")
        return voices

    # 2. Try spd-say -L for default module
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
            lines = proc.stdout.strip().splitlines()
            # If we get a ridiculous number of lines, the daemon is corrupted/flooded
            if len(lines) > 500:
                logger.warning("Spd-say returned %d voices (suspected flooding) — restarting daemon", len(lines))
                if not retrying and try_restart_speechd():
                    return _discover_spd_voices(retrying=True)

                # Still flooded after restart
                _is_speechd_broken = True
                logger.error("Speech-dispatcher still flooded after restart. Discarding main list.")
                return voices

            sys_lang_full = get_system_language()
            sys_lang = sys_lang_full.split("-")[0].split("_")[0]
            
            for line in lines:
                line = line.strip()
                if not line or "NAME" in line or "dummy" in line:
                    continue
                parts = re.split(r"\s{2,}", line)
                if len(parts) < 2:
                    continue
                voice_name = parts[0].strip()
                lang_code = parts[1].strip()
                
                # Double normalize check
                norm_id = _normalize_id(voice_name)
                if norm_id in known_ids:
                    continue
                
                # Filter out generic voices that don't match system language or English
                # This prevents showing hundreds of espeak variants for foreign languages
                voice_lang_short = lang_code.split("-")[0].split("_")[0]
                if voice_lang_short not in [sys_lang, "en"] and "rhvoice" not in voice_name.lower():
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
                known_ids.add(norm_id)
                
                # Hard limit
                if len(voices) > 200:
                    break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("spd-say not available")

    return voices

def _discover_rhvoice_voices() -> list[VoiceInfo]:
    """Discover native RHVoice voices from directory scan."""
    voices: list[VoiceInfo] = []
    voice_dirs = [
        Path("/usr/share/RHVoice/voices"),
        Path("/usr/local/share/RHVoice/voices"),
    ]

    # Map: normalized dir name → (ssip_name, language, gender, display_name)
    # ssip_name must match what speech-dispatcher uses in set_synthesis_voice
    known_voices: dict[str, tuple[str, str, str, str]] = {
        "leticia-f123": ("Leticia-F123", "pt-BR", "female", "Letícia F123"),
        "evgeniy-eng": ("Evgeniy-Eng", "en", "male", "Evgeniy Eng"),
    }

    # Map language names in voice.info to ISO codes
    lang_name_map = {
        "portuguese": "pt-BR",
        "brazilian-portuguese": "pt-BR",
        "brazilian portuguese": "pt-BR",
        "english": "en",
        "spanish": "es",
        "esperanto": "eo",
        "russian": "ru",
        "ukrainian": "uk",
        "tatar": "tt",
        "kyrgyz": "ky",
        "georgian": "ka",
        "czech": "cs",
        "polish": "pl",
    }

    for vdir in voice_dirs:
        if not vdir.exists():
            continue
        for entry in vdir.iterdir():
            if not entry.is_dir():
                continue
            dirname = entry.name
            
            # 1. Try metadata file first
            info_file = entry / "voice.info"
            ssip_name, lang, gender, display_name = None, None, None, None
            
            if info_file.exists():
                try:
                    info_text = info_file.read_text(encoding="utf-8")
                    info_data = {}
                    for line in info_text.splitlines():
                        if "=" in line:
                            key, val = line.split("=", 1)
                            info_data[key.strip().lower()] = val.strip()
                    
                    display_name = info_data.get("name", dirname)
                    ssip_name = display_name
                    raw_lang = info_data.get("language", "").lower()
                    lang = lang_name_map.get(raw_lang, raw_lang[:2] if raw_lang else "en")
                    gender = info_data.get("gender", _guess_gender(dirname))
                except Exception as e:
                    logger.debug("Error parsing %s: %s", info_file, e)

            # 2. Fallback to hardcoded knowledge
            if not lang:
                meta = known_voices.get(dirname.lower())
                if meta:
                    ssip_name, lang, gender, display_name = meta
                else:
                    ssip_name = dirname
                    lang = "en"
                    gender = _guess_gender(dirname)
                    display_name = dirname.replace("-", " ").replace("_", " ").title()

            voices.append(
                VoiceInfo(
                    voice_id=ssip_name,
                    name=display_name,
                    language=lang,
                    language_name=_lang_name(lang[:2]),
                    backend=TTSBackend.RHVOICE.value,
                    output_module="",
                    gender=gender,
                    quality="high",
                    description="RHVoice — high quality local synthesis",
                )
            )

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

            # Map: pkg_name → (ssip_name, language, gender, display_name)
            pkg_meta: dict[str, tuple[str, str, str, str]] = {
                "leticia-f123": ("Leticia-F123", "pt-BR", "female", "Letícia F123"),
                "evgeniy-eng": ("Evgeniy-Eng", "en", "male", "Evgeniy"),
                "alan": ("Alan", "en", "male", "Alan"),
                "mateo": ("Mateo", "es", "male", "Mateo"),
                "natalia": ("Natalia", "ru", "female", "Natalia"),
                "anna": ("Anna", "ru", "female", "Anna"),
                "elena": ("Elena", "ru", "female", "Elena"),
            }

            meta = pkg_meta.get(voice_name)
            if meta:
                voice_id, lang, gender, display = meta
            else:
                lang = "en"
                gender = _guess_gender(voice_name)
                display = voice_name.replace("-", " ").title()
                voice_id = voice_name.title().replace(" ", "-")
            voices.append(
                VoiceInfo(
                    voice_id=voice_id,
                    name=display,
                    language=lang,
                    language_name=_lang_name(lang[:2]),
                    backend=TTSBackend.RHVOICE.value,
                    output_module="",
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


def _discover_kokoro_voices() -> list[VoiceInfo]:
    """Discover Kokoro TTS voices.

    Kokoro voices are built into the model and don't need local files.
    We detect the koko CLI binary (from biglinux-kokoro-tts) or the
    kokoro Python package to confirm the engine is available, then
    return the known voice catalog.
    """
    voices: list[VoiceInfo] = []

    # Check if Kokoro is available via the koko CLI binary or Python package
    koko_available = shutil.which("koko") is not None
    if not koko_available:
        try:
            import kokoro as _  # noqa: F401
            koko_available = True
        except ImportError:
            pass

    if not koko_available:
        logger.debug("Kokoro not installed (no koko binary or kokoro Python package), skipping voice discovery")
        return voices

    # Try to discover voices dynamically from koko CLI
    discovered_from_cli = False
    try:
        proc = subprocess.run(
            ["koko", "voices"],
            capture_output=True, text=True, timeout=5,
            env={**os.environ,
                 "KOKO_MODEL_PATH": "/usr/share/biglinux-kokoro-tts/model/model.onnx",
                 "KOKO_DATA_PATH": str(get_active_voices_bin())},
        )
        if proc.returncode == 0:
            lang_map = {
                "chinese": "zh", "english (us)": "en-US", "english (gb)": "en-GB",
                "english": "en-US", "french": "fr", "hindi": "hi",
                "italian": "it", "japanese": "ja", "portuguese": "pt-BR",
                "spanish": "es",
            }

            lines = proc.stdout.splitlines()

            # Find the separator line (------) to determine column positions
            col_positions: list[tuple[int, int]] = []
            for line in lines:
                if line.startswith("---"):
                    pos = 0
                    for col in line.split():
                        end = pos + len(col)
                        col_positions.append((pos, end))
                        pos = end + 1  # +1 for the space separator
                    break

            if col_positions:
                for line in lines:
                    if not line.strip() or line.startswith("Voice ID") or line.startswith("---") or "loaded:" in line:
                        continue
                    # Extract fields using column positions
                    fields = []
                    for start, end in col_positions:
                        fields.append(line[start:end].strip() if start < len(line) else "")
                    # Last column extends to end of line
                    if col_positions and len(col_positions) >= 5:
                        fields[-1] = line[col_positions[-1][0]:].strip()

                    if len(fields) < 4:
                        continue
                    vid, name, lang_label, gender = fields[0], fields[1], fields[2], fields[3].lower()
                    desc = fields[4] if len(fields) > 4 else f"Kokoro {lang_label} voice"
                    lang_code = lang_map.get(lang_label.lower(), "en-US")

                    voices.append(
                        VoiceInfo(
                            voice_id=f"kokoro:{vid}",
                            name=name,
                            language=lang_code,
                            language_name=_lang_name(lang_code.split("-")[0]),
                            backend=TTSBackend.KOKORO.value,
                            output_module="",
                            gender=gender,
                            quality="neural",
                            description=f"Kokoro — {desc}",
                        )
                    )
                if voices:
                    discovered_from_cli = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback to hardcoded catalog if CLI parsing failed
    if not discovered_from_cli:
        kokoro_voices = [
            # Brazilian Portuguese
            ("pf_dora", "Dora", "pt-BR", "female", "Voz feminina neural em pt-BR"),
            ("pm_alex", "Alex", "pt-BR", "male", "Voz masculina neural em pt-BR"),
            ("pm_santa", "Santa", "pt-BR", "male", "Voz masculina alternativa pt-BR"),
            # American English
            ("af_heart", "Heart ❤️", "en-US", "female", "Primary reference voice (best quality)"),
            ("af_bella", "Bella", "en-US", "female", "Warm feminine voice"),
            ("af_nicole", "Nicole", "en-US", "female", "Calm feminine voice"),
            ("af_aoede", "Aoede", "en-US", "female", "Clear feminine voice"),
            ("af_kore", "Kore", "en-US", "female", "Bright feminine voice"),
            ("af_sarah", "Sarah", "en-US", "female", "Neutral feminine voice"),
            ("af_nova", "Nova", "en-US", "female", "Modern feminine voice"),
            ("af_sky", "Sky", "en-US", "female", "Youthful feminine voice"),
            ("af_river", "River", "en-US", "female", "Natural feminine voice"),
            ("am_adam", "Adam", "en-US", "male", "Deep masculine voice"),
            ("am_michael", "Michael", "en-US", "male", "Neutral masculine voice"),
            ("am_fenrir", "Fenrir", "en-US", "male", "Strong masculine voice"),
            ("am_liam", "Liam", "en-US", "male", "Friendly masculine voice"),
            ("am_echo", "Echo", "en-US", "male", "Clear masculine voice"),
            ("am_eric", "Eric", "en-US", "male", "Professional masculine voice"),
            ("am_onyx", "Onyx", "en-US", "male", "Deep masculine voice"),
            ("am_puck", "Puck", "en-US", "male", "Playful masculine voice"),
            ("am_santa", "Santa", "en-US", "male", "Warm masculine voice"),
            # British English
            ("bf_emma", "Emma", "en-GB", "female", "British feminine voice"),
            ("bf_isabella", "Isabella", "en-GB", "female", "British feminine voice"),
            ("bf_alice", "Alice", "en-GB", "female", "British feminine voice"),
            ("bf_lily", "Lily", "en-GB", "female", "British feminine voice"),
            ("bm_george", "George", "en-GB", "male", "British masculine voice"),
            ("bm_fable", "Fable", "en-GB", "male", "British masculine voice"),
            ("bm_lewis", "Lewis", "en-GB", "male", "British masculine voice"),
            ("bm_daniel", "Daniel", "en-GB", "male", "British masculine voice"),
            # Spanish
            ("ef_dora", "Dora", "es", "female", "Spanish feminine voice"),
            ("em_alex", "Alex", "es", "male", "Spanish masculine voice"),
            ("em_santa", "Santa", "es", "male", "Spanish masculine voice"),
            # French
            ("ff_siwis", "Siwis", "fr", "female", "French feminine voice"),
            # Italian
            ("if_sara", "Sara", "it", "female", "Italian feminine voice"),
            ("im_nicola", "Nicola", "it", "male", "Italian masculine voice"),
            # Japanese
            ("jf_alpha", "Alpha", "ja", "female", "Japanese feminine voice"),
            ("jf_gongitsune", "Gongitsune", "ja", "female", "Japanese feminine voice"),
            ("jf_nezumi", "Nezumi", "ja", "female", "Japanese feminine voice"),
            ("jf_tebukuro", "Tebukuro", "ja", "female", "Japanese feminine voice"),
            ("jm_kumo", "Kumo", "ja", "male", "Japanese masculine voice"),
            # Hindi
            ("hf_alpha", "Alpha", "hi", "female", "Hindi feminine voice"),
            ("hf_beta", "Beta", "hi", "female", "Hindi feminine voice"),
            ("hm_omega", "Omega", "hi", "male", "Hindi masculine voice"),
            ("hm_psi", "Psi", "hi", "male", "Hindi masculine voice"),
            # Mandarin Chinese
            ("zf_xiaobei", "Xiaobei", "zh", "female", "Chinese feminine voice"),
            ("zf_xiaoni", "Xiaoni", "zh", "female", "Chinese feminine voice"),
            ("zf_xiaoxiao", "Xiaoxiao", "zh", "female", "Chinese feminine voice"),
            ("zf_xiaoyi", "Xiaoyi", "zh", "female", "Chinese feminine voice"),
            ("zm_yunjian", "Yunjian", "zh", "male", "Chinese masculine voice"),
            ("zm_yunxi", "Yunxi", "zh", "male", "Chinese masculine voice"),
            ("zm_yunxia", "Yunxia", "zh", "male", "Chinese masculine voice"),
            ("zm_yunyang", "Yunyang", "zh", "male", "Chinese masculine voice"),
        ]

        for voice_id, name, lang, gender, desc in kokoro_voices:
            voices.append(
                VoiceInfo(
                    voice_id=f"kokoro:{voice_id}",
                    name=name,
                    language=lang,
                    language_name=_lang_name(lang[:2]),
                    backend=TTSBackend.KOKORO.value,
                    output_module="",
                    gender=gender,
                    quality="neural",
                    description=f"Kokoro — {desc}",
                )
            )

    logger.debug("Kokoro: discovered %d voices", len(voices))
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
    # 1. Try PATH
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

    # 2. Check known install locations directly (useful if PATH is restricted in GUI)
    for path in [
        "/usr/bin/piper-tts",
        "/usr/sbin/piper-tts",
        "/usr/local/bin/piper-tts",
        "/opt/piper-tts/piper",
        "/usr/bin/piper",
    ]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return None


def _guess_gender(name: str) -> str:
    """Best-effort gender guess from voice name."""
    name_lower = name.lower()
    female_names = {
        "letícia",
        "leticia",
        "natalia",
        "anna",
        "elena",
        "irina",
        "lyubov",
        "marianna",
        "hana",
        "suze",
        "magda",
        "clb",
        "slt",
        "spomenka",
        "natia",
    }
    male_names = {
        "antonio",
        "evgeniy",
        "alan",
        "bdl",
        "aleksandr",
        "artemiy",
        "anatol",
        "volodymyr",
        "zdenek",
        "natan",
        "kiko",
        "azamat",
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


def get_installed_tts_packages() -> list[dict[str, str]]:
    """
    Get a list of installed TTS-related packages on the system.
    Scans for rhvoice, espeak, and piper packages.
    """
    packages = []
    try:
        proc = subprocess.run(
            ["pacman", "-Qs", "rhvoice|espeak|piper"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            lines = proc.stdout.strip().splitlines()
            # pacman -Qs output:
            # local/name version (group)
            #     description
            current_pkg = None
            for line in lines:
                if line.startswith("local/"):
                    parts = line.split()
                    name = parts[0].removeprefix("local/")
                    version = parts[1]
                    current_pkg = {"name": name, "version": version}
                elif line.startswith("    ") and current_pkg:
                    current_pkg["description"] = line.strip()
                    packages.append(current_pkg)
                    current_pkg = None
    except Exception as e:
        logger.error("Failed to list installed TTS packages: %s", e)
    
    return packages
def get_supported_but_missing_voices() -> list[dict[str, str]]:
    """Detect languages that have RHVoice support installed but no voice packages."""
    missing = []
    lang_dir = Path("/usr/share/RHVoice/languages")
    voice_dir = Path("/usr/share/RHVoice/voices")
    
    if not lang_dir.exists():
        return []
        
    # Get names of installed voices (dirs)
    installed_voices = []
    if voice_dir.exists():
        installed_voices = [d.name.lower() for d in voice_dir.iterdir() if d.is_dir()]
        
    # Map of language support package to expected voice packages
    recommendations = {
        "polish": ("rhvoice-voice-magda", "Magda"),
        "russian": ("rhvoice-voice-anna", "Anna"),
        "ukrainian": ("rhvoice-voice-anatoliy", "Anatoliy"),
        "czech": ("rhvoice-voice-zdenek", "Zdenek"),
        "esperanto": ("rhvoice-voice-spomenka", "Spomenka"),
        "spanish": ("rhvoice-voice-mateo", "Mateo"),
    }
    
    for entry in lang_dir.iterdir():
        if not entry.is_dir():
            continue
        lang_name = entry.name.lower()
        
        # Check if any voice exists for this language
        # We look for a voice.info inside current voices or just some directory
        found = False
        if voice_dir.exists():
            for v_entry in voice_dir.iterdir():
                info_file = v_entry / "voice.info"
                if info_file.exists():
                    try:
                        content = info_file.read_text().lower()
                        if f"language={lang_name}" in content or f"language={lang_name.replace('-', ' ')}" in content:
                            found = True
                            break
                    except: pass
        
        if not found:
            pkg, voice_name_rec = recommendations.get(lang_name, ("rhvoice-voice-*", "Any"))
            missing.append({
                "language": entry.name,
                "pkg_needed": pkg,
                "voice_example": voice_name_rec
            })
            
    return missing
