"""
TTS service — speak and stop text using multiple backends.

Manages the TTS state machine: IDLE → SPEAKING → IDLE
Handles speak/stop toggle (Alt+V behavior).
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable
from typing import Any

from config import TTSBackend, TTSState, KokoroConfig
from services.text_processor import process_text
from services.voice_manager import VoiceInfo
from utils.speechd_utils import try_restart_speechd

logger = logging.getLogger(__name__)

# Native Rust TTS engine (optional — falls back to subprocess if unavailable)
_tts_engine = None

def _get_tts_engine():
    """Lazy-load the native Rust TTS engine module."""
    global _tts_engine
    if _tts_engine is None:
        try:
            import tts_engine as _mod
            _tts_engine = _mod
            logger.info("Native tts_engine loaded (v%s)", _mod.version())
        except ImportError:
            _tts_engine = False  # Sentinel: tried and failed
            logger.debug("Native tts_engine not available, using subprocess")
    return _tts_engine if _tts_engine else None

# ── Callbacks ────────────────────────────────────────────────────────

OnStateChanged = Callable[[TTSState], None]
OnProgress = Callable[[str], None]

# Watch interval in ms for process completion
_WATCH_INTERVAL_MS = 300


class TTSService:
    """
    Text-to-Speech service managing speak/stop lifecycle.

    Supports multiple backends: speech-dispatcher (spd-say),
    espeak-ng (direct), and Piper (neural).
    """

    def __init__(self, settings=None) -> None:
        self._state: TTSState = TTSState.IDLE
        self._process: subprocess.Popen[bytes] | None = None
        self._spd_client: Any = None  # speechd.SSIPClient
        self._on_state_changed: OnStateChanged | None = None
        self._on_state_changed_extra: list[OnStateChanged] = []
        self._on_progress: OnProgress | None = None
        self._watch_id: int = 0
        self._kokoro_pipeline: Any = None  # Unused, kept for compat
        self._kokoro_proc: subprocess.Popen[bytes] | None = None
        self._kokoro_thread: threading.Thread | None = None
        self._kokoro_stop_event = threading.Event()  # Signal to stop generation
        self._bg_thread: threading.Thread | None = None  # Generic background thread (Piper, etc.)
        self._last_spoken_text: str = ""  # For history
        self._last_backend: str = ""
        self._last_voice_id: str = ""
        self._settings = settings  # AppSettings reference

    @property
    def state(self) -> TTSState:
        """Current TTS state."""
        return self._state

    @property
    def is_speaking(self) -> bool:
        """Whether TTS is currently speaking."""
        if self._state == TTSState.SPEAKING:
            # Verify process is still running
            if self._process and self._process.poll() is not None:
                self._set_state(TTSState.IDLE)
                self._process = None
                return False
            return True
        return False

    def set_on_state_changed(self, callback: OnStateChanged | None) -> None:
        """Set callback for state changes."""
        self._on_state_changed = callback

    def add_on_state_changed(self, callback: OnStateChanged) -> None:
        """Add an additional state change listener (not replaced by set_on_state_changed)."""
        self._on_state_changed_extra.append(callback)

    def set_on_progress(self, callback: OnProgress | None) -> None:
        """Set callback for progress updates."""
        self._on_progress = callback

    def speak(
        self,
        text: str,
        *,
        voice: VoiceInfo | None = None,
        rate: int = -25,
        pitch: int = -25,
        volume: int = 75,
        backend: str = TTSBackend.SPEECH_DISPATCHER.value,
        output_module: str = "rhvoice",
        voice_id: str = "",
        expand_abbreviations: bool = True,
        process_special_chars: bool = True,
        process_urls: bool = False,
        strip_formatting: bool = True,
        stop_previous: bool = True,
    ) -> bool:
        """
        Speak the given text.

        Args:
            text: Text to speak.
            voice: VoiceInfo to use (overrides voice_id/backend).
            rate: Speech rate (-100 to 100).
            pitch: Speech pitch (-100 to 100).
            volume: Speech volume (0 to 100).
            backend: TTS backend to use.
            output_module: Output module (for speech-dispatcher).
            voice_id: Voice identifier.
            expand_abbreviations: Expand common abbreviations.
            process_special_chars: Read special chars aloud.
            process_urls: Read URLs aloud.
            strip_formatting: Remove markdown/HTML.
            stop_previous: Stop any current speech before starting (default True).

        Returns:
            True if speech started successfully.
        """
        if not text or not text.strip():
            logger.debug("No text to speak")
            return False

        if stop_previous:
            # Always stop any previous speech (even if state tracking says idle,
            # a background thread might still be alive between chunks)
            was_speaking = self.is_speaking
            self.stop()
            if was_speaking:
                # Brief pause to let the output module fully release
                import time

                time.sleep(0.15)

        # Resolve voice parameters
        if voice:
            voice_id = voice.voice_id
            backend = voice.backend
            output_module = voice.output_module

        # Process text
        processed = process_text(
            text,
            expand_abbreviations=expand_abbreviations,
            process_special_chars=process_special_chars,
            process_urls=process_urls,
            strip_formatting=strip_formatting,
        )

        if not processed:
            logger.debug("Text is empty after processing")
            return False

        logger.debug("Processed text: %r", processed[:80])

        # Speak via appropriate backend
        if backend == TTSBackend.SPEECH_DISPATCHER.value:
            success = self._speak_spd(
                processed, voice_id, output_module, rate, pitch, volume
            )
        elif backend == TTSBackend.RHVOICE.value:
            success = self._speak_rhvoice(processed, voice_id, rate, pitch, volume)
        elif backend == TTSBackend.ESPEAK_NG.value:
            success = self._speak_espeak(processed, voice_id, rate, pitch, volume)
        elif backend == TTSBackend.PIPER.value:
            success = self._speak_piper(processed, voice_id, rate, pitch, volume)
        elif backend == TTSBackend.KOKORO.value:
            success = self._speak_kokoro(processed, voice_id, rate, pitch, volume)
        else:
            logger.error("Unknown backend: %s", backend)
            return False

        if success:
            self._last_spoken_text = processed
            self._last_backend = backend
            self._last_voice_id = voice_id
            self._set_state(TTSState.SPEAKING)
            self._start_watch()
            if self._on_progress:
                self._on_progress(processed[:100])
        else:
            self._set_state(TTSState.ERROR)

        return success

    def stop(self) -> None:
        """Stop current speech immediately."""
        self._stop_watch()

        # Stop speech-dispatcher via SSIP API
        if self._spd_client:
            try:
                self._spd_client.cancel()
            except Exception:
                pass
            self._close_spd_client()

        # Cancel speech-dispatcher queue via CLI (fallback)
        try:
            subprocess.run(
                ["spd-say", "-C"],
                capture_output=True,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Kill running process
        if self._process:
            try:
                self._process.send_signal(signal.SIGTERM)
                self._process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
            self._process = None

        # Kill RHVoice sub-process if active
        rh = getattr(self, "_rh_proc", None)
        if rh:
            try:
                rh.kill()
            except ProcessLookupError:
                pass
            self._rh_proc = None

        # Kill Piper sub-process if active
        piper = getattr(self, "_piper_proc", None)
        if piper:
            try:
                piper.kill()
            except ProcessLookupError:
                pass
            self._piper_proc = None

        # Clean up Piper temp audio file
        tmp_path = getattr(self, "_piper_tmp_path", None)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            self._piper_tmp_path = None

        # Kill Kokoro sub-process if active
        kokoro = getattr(self, "_kokoro_proc", None)
        if kokoro:
            try:
                kokoro.kill()
            except ProcessLookupError:
                pass
            self._kokoro_proc = None

        # Signal and join Kokoro thread
        self._kokoro_stop_event.set()
        kt = self._kokoro_thread
        if kt and kt.is_alive():
            kt.join(timeout=2)
        self._kokoro_thread = None

        # Join generic background thread (Piper, etc.)
        bt = self._bg_thread
        if bt and bt.is_alive():
            bt.join(timeout=2)
        self._bg_thread = None

        # Also kill any lingering backends
        self._kill_backends()

        self._set_state(TTSState.IDLE)
        logger.debug("Speech stopped")

    def toggle(
        self,
        text: str,
        **kwargs: Any,
    ) -> bool:
        """
        Toggle speak/stop — the Alt+V behavior.

        If speaking → stop.
        If idle + text → speak.
        If idle + no text → return False.

        Returns:
            True if state changed.
        """
        if self.is_speaking:
            self.stop()
            return True

        if text and text.strip():
            return self.speak(text, **kwargs)

        return False

    def _speak_spd(
        self,
        text: str,
        voice_id: str,
        output_module: str,
        rate: int,
        pitch: int,
        volume: int,
    ) -> bool:
        """Speak via speech-dispatcher using the Python SSIP API directly.

        Uses the `speechd` Python module for reliable text delivery,
        bypassing spd-say which can drop text in some configurations.
        """
        try:
            import speechd
        except ImportError:
            logger.warning("speechd module not available, falling back to spd-say")
            return self._speak_spd_fallback(
                text, voice_id, output_module, rate, pitch, volume
            )

        try:
            # Close previous connection if any
            self._close_spd_client()

            client = speechd.SSIPClient("biglinux-tts")
            self._spd_client = client

            if output_module:
                client.set_output_module(output_module)
            if voice_id:
                client.set_synthesis_voice(voice_id)

            # speechd rate/pitch: -100 to +100, volume: -100 to +100
            client.set_rate(max(-100, min(100, rate)))
            client.set_pitch(max(-100, min(100, pitch)))
            # Our volume is 0-100, speechd wants -100 to +100
            spd_vol = max(-100, min(100, (volume * 2) - 100))
            client.set_volume(spd_vol)

            # Speak with end callback to detect completion
            def on_end(callback_type: Any, index_mark: Any = None) -> None:
                try:
                    from gi.repository import GLib

                    GLib.idle_add(lambda: self._on_spd_finished() or False)
                except Exception:
                    self._on_spd_finished()

            client.speak(text, callback=on_end)

            logger.debug(
                "speechd: module=%s, voice=%s, rate=%d, pitch=%d, vol=%d, text=%r",
                output_module,
                voice_id,
                rate,
                pitch,
                spd_vol,
                text[:60],
            )
            return True

        except Exception as e:
            logger.error("speechd failed: %s", e)
            self._close_spd_client()
            # Try restarting speech-dispatcher and retry once
            if self._try_restart_speechd():
                try:
                    client = speechd.SSIPClient("biglinux-tts")
                    self._spd_client = client
                    if output_module:
                        client.set_output_module(output_module)
                    if voice_id:
                        client.set_synthesis_voice(voice_id)
                    client.set_rate(max(-100, min(100, rate)))
                    client.set_pitch(max(-100, min(100, pitch)))
                    spd_vol = max(-100, min(100, (volume * 2) - 100))
                    client.set_volume(spd_vol)

                    def on_end2(callback_type: Any, index_mark: Any = None) -> None:
                        try:
                            from gi.repository import GLib

                            GLib.idle_add(lambda: self._on_spd_finished() or False)
                        except Exception:
                            self._on_spd_finished()

                    client.speak(text, callback=on_end2)
                    logger.info("speechd retry succeeded after restart")
                    return True
                except Exception as e2:
                    logger.error("speechd retry also failed: %s", e2)
                    self._close_spd_client()
            # Fallback to spd-say
            return self._speak_spd_fallback(
                text, voice_id, output_module, rate, pitch, volume
            )

    def _try_restart_speechd(self) -> bool:
        """Helper to call the shared restart utility."""
        return try_restart_speechd()

    def _on_spd_finished(self) -> bool:
        """Called when speech-dispatcher finishes speaking (main thread)."""
        self._close_spd_client()
        self._set_state(TTSState.IDLE)
        return False  # Don't repeat

    def _close_spd_client(self) -> None:
        """Safely close the speechd client."""
        client = self._spd_client
        self._spd_client = None
        if client:
            try:
                client.close()
            except (RuntimeError, Exception):
                # RuntimeError: "cannot join current thread" when closing
                # from the speechd callback thread — safe to ignore
                pass

    def _speak_spd_fallback(
        self,
        text: str,
        voice_id: str,
        output_module: str,
        rate: int,
        pitch: int,
        volume: int,
    ) -> bool:
        """Fallback: speak via spd-say CLI when speechd module is unavailable."""
        cmd = ["spd-say", "--wait"]

        if output_module:
            cmd.extend(["-o", output_module])
        if voice_id:
            cmd.extend(["-y", voice_id])
        if rate != 0:
            cmd.extend(["-r", str(rate)])
        if pitch != 0:
            cmd.extend(["-p", str(pitch)])
        if volume != 0:
            spd_vol = max(-100, min(100, (volume * 2) - 100))
            cmd.extend(["-i", str(spd_vol)])

        # Use -- to force text as positional argument
        cmd.append("--")
        cmd.append(text)

        logger.debug("spd-say fallback cmd: %s", cmd)
        return self._start_process_no_stdin(cmd)

    def _speak_rhvoice(
        self,
        text: str,
        voice_id: str,
        rate: int,
        pitch: int,
        volume: int,
    ) -> bool:
        """Speak via RHVoice-test directly, bypassing speech-dispatcher."""
        cmd = ["RHVoice-test"]

        if voice_id:
            cmd.extend(["-p", voice_id])

        # RHVoice rate and pitch are percentages where 100 is normal.
        # Our variables are (-100 to 100), so 0 is normal.
        # Translating: -100 -> 0% (in practice let's cap at 20%), 100 -> 200%
        rh_rate = 100 + rate
        rh_rate = max(20, min(300, rh_rate))
        cmd.extend(["-r", str(rh_rate)])

        rh_pitch = 100 + pitch
        rh_pitch = max(20, min(200, rh_pitch))
        cmd.extend(["-t", str(rh_pitch)])

        # Volume is naturally a percentage in our config
        cmd.extend(["-v", str(volume)])

        # RHVoice-test does NOT play directly, it writes to a file or stdout.
        # We pipe its stdout to aplay for immediate playback.
        cmd.extend(["-o", "/dev/stdout"])

        logger.debug("RHVoice direct cmd: %s | aplay", cmd)
        
        try:
            # We chain the two commands: RHVoice-test | aplay
            rh_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            
            play_proc = subprocess.Popen(
                ["aplay", "-q"],
                stdin=rh_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Send text to RHVoice-test stdin
            if rh_proc.stdin:
                rh_proc.stdin.write(text.encode("utf-8"))
                rh_proc.stdin.close()

            # The standard process tracker will watch play_proc
            self._process = play_proc
            self._rh_proc = rh_proc # Keep ref to kill if needed
            return True

        except (FileNotFoundError, OSError) as e:
            logger.error("Failed to start RHVoice native: %s", e)
            return False

    def _speak_espeak(
        self,
        text: str,
        voice_id: str,
        rate: int,
        pitch: int,
        volume: int,
    ) -> bool:
        """Speak via espeak-ng (native FFI or subprocess fallback)."""
        # Extract actual voice name from our ID format
        actual_voice = (
            voice_id.removeprefix("espeak-")
            if voice_id.startswith("espeak-")
            else voice_id
        )

        # Convert from UI range to espeak-ng raw parameters
        wpm = max(80, min(450, 175 + int(rate * 1.5)))
        esp_pitch = max(0, min(99, 50 + int(pitch * 0.5)))
        esp_vol = min(200, max(10, int(volume * 2)) if volume > 0 else 10)

        # Try native Rust engine first
        engine = _get_tts_engine()
        if engine:
            try:
                return engine.speak_espeak(
                    text, actual_voice or "en", wpm, esp_pitch, esp_vol,
                )
            except Exception as e:
                logger.warning("Native espeak failed, falling back: %s", e)

        # Subprocess fallback
        cmd = ["espeak-ng"]
        if actual_voice:
            cmd.extend(["-v", actual_voice])
        cmd.extend(["-s", str(wpm)])
        cmd.extend(["-p", str(esp_pitch)])
        cmd.extend(["-a", str(esp_vol)])
        cmd.append(text)

        return self._start_process_no_stdin(cmd)

    def _speak_piper(
        self, text: str, voice_id: str, rate: int, pitch: int, volume: int
    ) -> bool:
        """Speak via Piper neural TTS (native ONNX or subprocess fallback).

        voice_id format: "piper:/absolute/path/to/model.onnx"

        Strategy: synthesize WAV to temp file, then play via aplay/sox.
        Native engine (tts_engine.synthesize_piper) is ~7x faster for short text
        due to cached model + no subprocess overhead.

        Rate/Pitch/Volume mapping:
          rate (-100..100) → length_scale: -100=0.3 (fast), 0=1.0, 100=2.5 (slow)
          pitch (-100..100) → noise_scale: maps to voice expressiveness
          volume (0..100) → volume factor
        """
        import tempfile

        # Extract model path from voice_id
        model_path = (
            voice_id.removeprefix("piper:")
            if voice_id.startswith("piper:")
            else voice_id
        )

        if not os.path.isfile(model_path):
            logger.error("Piper model not found: %s", model_path)
            return False

        # Convert rate (-100..100) to length_scale
        if rate >= 0:
            length_scale = 1.0 - (rate / 100.0) * 0.7  # 1.0 → 0.3
        else:
            length_scale = 1.0 - (rate / 100.0) * 1.5  # 1.0 → 2.5

        # Convert pitch (-100..100) to noise_scale
        noise_scale = 0.667 + (pitch / 100.0) * 0.333
        noise_w = 0.8

        # Volume factor (0..100 → 0.2..2.0)
        vol_factor = max(0.2, min(2.0, volume / 50.0)) if volume > 0 else 0.2

        # Create temp file for audio
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()

        engine = _get_tts_engine()

        def _generate_native() -> bool:
            """Synthesize via native Rust ONNX engine."""
            try:
                wav_bytes = engine.synthesize_piper(
                    text, model_path, length_scale, noise_scale, noise_w, vol_factor,
                )
                if not wav_bytes or len(wav_bytes) < 100:
                    return False
                with open(tmp_path, "wb") as f:
                    f.write(wav_bytes)
                return True
            except Exception as e:
                logger.warning("Native Piper synthesis failed: %s", e)
                return False

        def _generate_subprocess() -> bool:
            """Synthesize via piper-tts subprocess (fallback)."""
            from services.voice_manager import _find_piper_binary

            piper_bin = _find_piper_binary()
            if not piper_bin:
                logger.error("Piper binary not found (tried piper-tts, piper)")
                return False

            cmd_piper = [
                piper_bin, "--model", model_path,
                "--output_file", tmp_path,
                "--length_scale", f"{length_scale:.2f}",
                "--noise_scale", f"{noise_scale:.3f}",
                "--noise_w", f"{noise_w:.2f}",
                "--sentence_silence", "0.05",
            ]

            gen_proc = subprocess.Popen(
                cmd_piper,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._piper_proc = gen_proc

            if gen_proc.stdin:
                gen_proc.stdin.write(text.encode("utf-8"))
                gen_proc.stdin.close()

            gen_proc.wait()

            if gen_proc.returncode != 0:
                if gen_proc.returncode == -9:
                    logger.debug("Piper stopped by user")
                else:
                    stderr = (
                        gen_proc.stderr.read().decode("utf-8", errors="replace")
                        if gen_proc.stderr else ""
                    )
                    logger.error(
                        "Piper generation failed (code %d): %s",
                        gen_proc.returncode, stderr[-200:],
                    )
                return False

            if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) < 100:
                logger.error("Piper generated empty or missing audio file")
                return False

            return True

        def _generate_and_play() -> None:
            try:
                # Phase 1: synthesize (native or subprocess)
                ok = False
                if engine:
                    logger.debug(
                        "Piper native: model=%s length_scale=%.2f noise_scale=%.3f",
                        model_path, length_scale, noise_scale,
                    )
                    ok = _generate_native()

                if not ok:
                    logger.debug("Piper subprocess fallback: model=%s", model_path)
                    ok = _generate_subprocess()

                if not ok:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    self._set_state(TTSState.ERROR)
                    return

                # Phase 2: play the pre-generated audio
                # Native already applied volume, subprocess needs sox
                if not engine and vol_factor != 1.0:
                    sox_available = (
                        subprocess.run(
                            ["which", "sox"],
                            capture_output=True, timeout=2,
                        ).returncode == 0
                    )
                    if sox_available:
                        play_cmd = [
                            "play", "-q", tmp_path, "vol", f"{vol_factor:.2f}",
                        ]
                    else:
                        play_cmd = ["aplay", "-q", tmp_path]
                else:
                    play_cmd = ["aplay", "-q", tmp_path]

                play_proc = subprocess.Popen(
                    play_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._piper_proc = None
                self._process = play_proc
                self._piper_tmp_path = tmp_path

            except (FileNotFoundError, OSError) as e:
                logger.error("Failed to start Piper: %s", e)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                self._set_state(TTSState.ERROR)

        thread = threading.Thread(target=_generate_and_play, daemon=True)
        self._bg_thread = thread
        thread.start()
        return True

    def _speak_kokoro(
        self, text: str, voice_id: str, rate: int, pitch: int, volume: int
    ) -> bool:
        """Speak via Kokoro neural TTS using the Python API.

        voice_id format: "kokoro:af_heart" or "kokoro:pf_dora"
        Uses kokoro.KPipeline to generate audio, then plays via aplay/sox.
        """
        import tempfile

        try:
            from kokoro import KPipeline
            import soundfile as sf
        except ImportError:
            logger.error("Kokoro library not installed — pip install kokoro soundfile")
            return False

        # Read Kokoro-specific settings
        kokoro_cfg = self._settings.speech.kokoro if self._settings else None

        # Extract voice name from voice_id
        kokoro_voice = (
            voice_id.removeprefix("kokoro:")
            if voice_id.startswith("kokoro:")
            else voice_id
        )
        if not kokoro_voice:
            kokoro_voice = "pf_dora"  # Default Brazilian Portuguese

        # Determine lang_code — single-letter code for KPipeline
        voice_prefix = kokoro_voice[:1] if kokoro_voice else "p"
        lang_code = voice_prefix  # KPipeline uses single-letter codes directly
        if kokoro_cfg and kokoro_cfg.lang_code:
            lang_code = kokoro_cfg.lang_code

        # ── Speed calculation ──
        # Base speed from rate slider: (-100..100) → (0.5..2.0)
        base_speed = 1.0 + (rate / 100.0)
        base_speed = max(0.5, min(2.0, base_speed))

        # Apply emotion preset speed modifier
        _EMOTION_SPEED = {
            "neutral": 1.0,
            "happy": 1.1,
            "calm": 0.8,
            "urgent": 1.4,
            "narrative": 0.9,
        }
        emotion = kokoro_cfg.emotion_preset if kokoro_cfg else "neutral"
        emotion_factor = _EMOTION_SPEED.get(emotion, 1.0)
        speed = max(0.5, min(2.0, base_speed * emotion_factor))

        # Volume factor (0..100 → 0.2..2.0)
        vol_factor = max(0.2, min(2.0, volume / 50.0)) if volume > 0 else 0.2

        logger.debug(
            "Kokoro: voice=%s, lang=%s, speed=%.2f, "
            "emotion=%s, vol=%.2f, text=%r",
            kokoro_voice, lang_code, speed,
            emotion, vol_factor, text[:60],
        )

        # Create or reuse cached pipeline
        cached_lang = getattr(self, "_kokoro_cached_lang", None)
        if cached_lang != lang_code or not hasattr(self, "_kokoro_api_pipeline"):
            try:
                self._kokoro_api_pipeline = KPipeline(lang_code=lang_code)
                self._kokoro_cached_lang = lang_code
            except Exception as e:
                logger.error("Failed to create KPipeline: %s", e)
                return False

        pipeline = self._kokoro_api_pipeline

        # Check sox availability for volume control
        sox_available = shutil.which("sox") is not None

        def _build_play_cmd(wav_path: str) -> list[str]:
            if vol_factor != 1.0 and sox_available:
                return ["play", "-q", wav_path, "vol", f"{vol_factor:.2f}"]
            return ["aplay", "-q", wav_path]

        def _generate_and_play() -> None:
            """Generate audio via KPipeline and play chunks sequentially."""
            tmp_paths: list[str] = []
            play_proc: subprocess.Popen | None = None

            try:
                generator = pipeline(text, voice=kokoro_voice, speed=speed)

                for _gs, _ps, audio in generator:
                    if self._kokoro_stop_event.is_set():
                        if play_proc:
                            play_proc.terminate()
                        return

                    if audio is None or len(audio) < 100:
                        continue

                    # Write audio chunk to temp WAV
                    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                    path = tmp.name
                    tmp.close()
                    tmp_paths.append(path)
                    sf.write(path, audio, 24000)

                    # Wait for previous chunk to finish playing
                    if play_proc:
                        while play_proc.poll() is None:
                            if self._kokoro_stop_event.is_set():
                                play_proc.terminate()
                                return
                            self._kokoro_stop_event.wait(timeout=0.05)

                    # Start playing this chunk
                    play_cmd = _build_play_cmd(path)
                    play_proc = subprocess.Popen(
                        play_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._process = play_proc

                # Wait for last chunk
                if play_proc:
                    while play_proc.poll() is None:
                        if self._kokoro_stop_event.is_set():
                            play_proc.terminate()
                            return
                        self._kokoro_stop_event.wait(timeout=0.05)

            except Exception as e:
                logger.error("Kokoro generation failed: %s", e)
                if play_proc and play_proc.poll() is None:
                    play_proc.terminate()
                self._set_state(TTSState.ERROR)
            finally:
                for p in tmp_paths:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

        self._kokoro_stop_event.clear()
        self._kokoro_thread = threading.Thread(
            target=_generate_and_play, daemon=True
        )
        self._kokoro_thread.start()
        return True

    def _start_process(self, cmd: list[str], text: str) -> bool:
        """Start a TTS process with text piped to stdin."""
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.stdin:
                proc.stdin.write(text.encode("utf-8"))
                proc.stdin.close()

            self._process = proc
            return True

        except FileNotFoundError:
            logger.error("Command not found: %s", cmd[0])
            return False
        except OSError as e:
            logger.error("Failed to start TTS: %s", e)
            return False

    def _start_process_no_stdin(self, cmd: list[str]) -> bool:
        """Start a TTS process without stdin (text passed as argument)."""
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._process = proc
            return True

        except FileNotFoundError:
            logger.error("Command not found: %s", cmd[0])
            return False
        except OSError as e:
            logger.error("Failed to start TTS: %s", e)
            return False

    def _kill_backends(self) -> None:
        """Kill any lingering TTS backend processes owned by us.

        Only kills espeak-ng, piper processes and RHVoice, which we launch directly.
        Never kills speech-dispatcher components (sd_rhvoice, spd-say) as
        those are managed by the speech-dispatcher daemon.
        """
        for proc_name in ["espeak-ng", "piper-tts", "RHVoice-test"]:
            try:
                subprocess.run(
                    ["pkill", "-f", proc_name],
                    capture_output=True,
                    timeout=2,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

    def _has_active_bg_thread(self) -> bool:
        """Check if any background TTS thread is still alive."""
        if self._kokoro_thread is not None and self._kokoro_thread.is_alive():
            return True
        if self._bg_thread is not None and self._bg_thread.is_alive():
            return True
        return False

    def _start_watch(self) -> None:
        """Start polling for process completion."""
        self._stop_watch()
        try:
            from gi.repository import GLib

            self._watch_id = GLib.timeout_add(_WATCH_INTERVAL_MS, self._check_process)
        except ImportError:
            pass

    def _stop_watch(self) -> None:
        """Stop the process watcher."""
        if self._watch_id:
            try:
                from gi.repository import GLib

                GLib.source_remove(self._watch_id)
            except (ImportError, ValueError):
                pass
            self._watch_id = 0

    def _check_process(self) -> bool:
        """Check if the TTS process has finished."""
        # If using speechd API, completion is handled by callback
        if self._spd_client is not None:
            return True  # Keep polling (speechd callback handles state)
        if self._process and self._process.poll() is not None:
            rc = self._process.returncode
            # Log stderr if available
            if self._process.stderr:
                try:
                    stderr_data = self._process.stderr.read()
                    if stderr_data:
                        logger.debug(
                            "TTS stderr: %s",
                            stderr_data.decode(errors="replace").strip(),
                        )
                except Exception:
                    pass
            if rc != 0:
                logger.warning("TTS process exited with code %d", rc)
            self._process = None

            # Background thread (Kokoro multi-chunk, Piper generation) may
            # still be alive — don't set IDLE yet, keep polling
            if self._has_active_bg_thread():
                return True

            # Clean up Piper temp audio file after playback
            tmp_path = getattr(self, "_piper_tmp_path", None)
            if tmp_path:
                # Save history before cleanup
                self._maybe_save_history(tmp_path)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                self._piper_tmp_path = None
            self._set_state(TTSState.IDLE)
            self._watch_id = 0
            return False  # Stop the timer
        if self._process is None:
            # Background thread may still be generating audio (no process yet)
            if self._has_active_bg_thread():
                return True  # Keep polling — generation in progress
            self._set_state(TTSState.IDLE)
            self._watch_id = 0
            return False
        return True  # Keep polling

    def _set_state(self, state: TTSState) -> None:
        """Update state and notify listeners."""
        if state != self._state:
            old = self._state
            self._state = state
            logger.debug("TTS state: %s → %s", old, state)
            if self._on_state_changed:
                self._on_state_changed(state)
            for cb in self._on_state_changed_extra:
                try:
                    cb(state)
                except Exception as e:
                    logger.warning("State change listener error: %s", e)

    def _maybe_save_history(self, audio_path: str | None = None) -> None:
        """Save history entry if history is enabled in settings."""
        if not self._settings:
            return
        history = getattr(self._settings, "history", None)
        if not history or not history.enabled:
            return
        if not self._last_spoken_text:
            return

        try:
            from services.history_service import save_history_entry

            save_history_entry(
                text=self._last_spoken_text,
                audio_path=audio_path,
                backend=self._last_backend,
                voice_id=self._last_voice_id,
                save_audio=history.save_audio,
                save_text=history.save_text,
            )
        except Exception as e:
            logger.error("Failed to save history: %s", e)

    def cleanup(self) -> None:
        """Clean up resources on shutdown."""
        self._stop_watch()
        self._close_spd_client()
        if self.is_speaking:
            self.stop()
