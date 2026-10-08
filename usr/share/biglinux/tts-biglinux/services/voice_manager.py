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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from config import TTSBackend
from services.kokoro_voice_service import get_active_voices_bin, koko_workdir, kokoro_model_path
from utils.i18n import _, language_name

logger = logging.getLogger(__name__)

# Global flag to avoid repeated hammering of a broken daemon in a single session

# ── Voice Metadata ───────────────────────────────────────────────────


@dataclass
class VoiceInfo:
    """Metadata for a single TTS voice."""

    voice_id: str
    name: str
    language: str
    language_name: str
    backend: str
    gender: str = ""  # male, female, neutral
    quality: str = "standard"  # standard, neural, high
    description: str = ""


@dataclass
class VoiceCatalog:
    """Complete catalog of available voices, grouped by language."""

    voices: list[VoiceInfo] = field(default_factory=list)
    backends_available: list[str] = field(default_factory=list)
    # Per selectable engine: is the program installed, and does it have voices?
    engines: dict[str, EngineAvailability] = field(default_factory=dict)

    def get_by_backend(self, backend: str) -> list[VoiceInfo]:
        """Get voices from a specific backend."""
        return [v for v in self.voices if v.backend == backend]

# ── Voice Discovery ──────────────────────────────────────────────────


class EngineAvailability(str, Enum):
    """What is installed for an engine (not whether it is speaking)."""

    NOT_INSTALLED = "not-installed"  # the program itself is missing
    NO_VOICES = "no-voices"  # installed, but no voice to speak with
    READY = "ready"  # installed with at least one voice


def _engine_installed(backend: str) -> bool:
    """Is the program for ``backend`` present? (blocking: worker thread)"""
    if backend == TTSBackend.RHVOICE.value:
        return shutil.which("RHVoice-test") is not None
    if backend == TTSBackend.ESPEAK_NG.value:
        return shutil.which("espeak-ng") is not None
    if backend == TTSBackend.PIPER.value:
        try:
            import tts_engine  # noqa: F401  (native Piper)
            return True
        except ImportError:
            return _find_piper_binary() is not None
    if backend == TTSBackend.KOKORO.value:
        from services.kokoro_voice_service import is_kokoro_installed

        return is_kokoro_installed() and kokoro_model_path().is_file()
    return False


def engine_availability(catalog: VoiceCatalog) -> dict[str, EngineAvailability]:
    """Availability of the four selectable engines (blocking: worker thread)."""
    result: dict[str, EngineAvailability] = {}
    for backend in (
        TTSBackend.RHVOICE.value,
        TTSBackend.ESPEAK_NG.value,
        TTSBackend.PIPER.value,
        TTSBackend.KOKORO.value,
    ):
        if not _engine_installed(backend):
            result[backend] = EngineAvailability.NOT_INSTALLED
        elif catalog.get_by_backend(backend):
            result[backend] = EngineAvailability.READY
        else:
            result[backend] = EngineAvailability.NO_VOICES
    return result


def discover_voices() -> VoiceCatalog:
    """Discover the voices of every installed engine, in parallel.

    speech-dispatcher is never queried: ``spd-say -L`` starts the daemon,
    which starts every installed output module, and some of them speak.
    """
    catalog = VoiceCatalog()

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_espeak = executor.submit(_discover_espeak_voices)
        future_piper = executor.submit(_discover_piper_voices)
        future_rhvoice = executor.submit(_discover_rhvoice_voices)
        future_kokoro = executor.submit(_discover_kokoro_voices)

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

    try:
        catalog.engines = engine_availability(catalog)
    except Exception as e:
        logger.error("Engine availability check failed: %s", e)

    logger.info(
        "Discovered %d voices from %d backends",
        len(catalog.voices),
        len(catalog.backends_available),
    )

    return catalog


def voice_language(backend: str, voice_id: str) -> str:
    """Language ("pt-BR", "en"…) of a configured voice; "" if unknown.

    Text rules (numbers, abbreviations, symbols) follow the voice, not the
    system: a pt-BR desktop reading English with an English voice.
    """
    if backend == TTSBackend.PIPER.value:
        code = Path(voice_id.removeprefix("piper:")).name.split("-", 1)[0]  # pt_BR-faber-medium.onnx
        return code.replace("_", "-") if re.fullmatch(r"[a-z]{2,3}(_[A-Z]{2})?", code) else ""
    if backend == TTSBackend.ESPEAK_NG.value:
        return voice_id.removeprefix("espeak-")
    if backend == TTSBackend.KOKORO.value:
        from services.kokoro_voice_service import koko_language

        return koko_language(voice_id)
    if backend == TTSBackend.RHVOICE.value:
        for voice in _discover_rhvoice_voices():
            if voice.voice_id.lower() == voice_id.lower():
                return voice.language
    return ""


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
                    language_name=language_name(lang),
                    backend=TTSBackend.RHVOICE.value,
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
                    language_name=language_name(lang),
                    backend=TTSBackend.RHVOICE.value,
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
                language_name=language_name(lang_code),
                backend=TTSBackend.ESPEAK_NG.value,
                gender=gender,
                quality="standard",
            )
        )

    return voices


def _discover_kokoro_voices() -> list[VoiceInfo]:
    """Discover Kokoro TTS voices.

    Kokoro voices are built into the model and don't need local files.
    The koko binary (package biglinux-kokoro-tts) confirms the engine is
    available; the known voice catalog is returned.
    """
    voices: list[VoiceInfo] = []

    if shutil.which("koko") is None:
        logger.debug("Kokoro not installed (no koko binary), skipping voice discovery")
        return voices

    # Try to discover voices dynamically from koko CLI
    discovered_from_cli = False
    try:
        proc = subprocess.run(
            ["koko", "voices"],
            capture_output=True, text=True, timeout=5,
            env={**os.environ,
                 "KOKO_MODEL_PATH": str(kokoro_model_path()),
                 "KOKO_DATA_PATH": str(get_active_voices_bin())},
            cwd=koko_workdir(),
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
                            language_name=language_name(lang_code),
                            backend=TTSBackend.KOKORO.value,
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
                    language_name=language_name(lang),
                    backend=TTSBackend.KOKORO.value,
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
                "x_low": _("Extra low quality"),
                "low": _("Low quality"),
                "medium": _("Medium quality"),
                "high": _("High quality"),
            }.get(quality, quality.title())

            voices.append(
                VoiceInfo(
                    voice_id=voice_id,
                    name=f"{speaker.title()} ({quality_label})",
                    language=lang_code,
                    language_name=language_name(lang_short),
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


