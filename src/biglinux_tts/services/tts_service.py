"""
TTS service — speak and stop text using multiple backends.

Manages the TTS state machine: IDLE → SPEAKING → IDLE
Handles speak/stop toggle (Alt+V behavior).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
from collections.abc import Callable
from typing import Any

from biglinux_tts.config import TTSBackend, TTSState
from biglinux_tts.services.text_processor import get_system_language, process_text
from biglinux_tts.services.voice_manager import VoiceCatalog, VoiceInfo

logger = logging.getLogger(__name__)

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

    def __init__(self) -> None:
        self._state: TTSState = TTSState.IDLE
        self._process: subprocess.Popen[bytes] | None = None
        self._spd_client: Any = None  # speechd.SSIPClient
        self._on_state_changed: OnStateChanged | None = None
        self._on_progress: OnProgress | None = None
        self._watch_id: int = 0

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

        Returns:
            True if speech started successfully.
        """
        if not text or not text.strip():
            logger.debug("No text to speak")
            return False

        # Stop any current speech first
        if self.is_speaking:
            self.stop()

        # Resolve voice parameters
        if voice:
            voice_id = voice.voice_id
            backend = voice.backend
            output_module = voice.output_module

        print(f"★ speak() → backend={backend!r}, voice_id={voice_id!r}, module={output_module!r}")
        print(f"★ speak() text={text[:80]!r}, volume={volume}")

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
        print(f"★ Dispatching to backend={backend!r} (spd={TTSBackend.SPEECH_DISPATCHER.value!r}, espeak={TTSBackend.ESPEAK_NG.value!r}, piper={TTSBackend.PIPER.value!r})")
        if backend == TTSBackend.SPEECH_DISPATCHER.value:
            success = self._speak_spd(processed, voice_id, output_module, rate, pitch, volume)
        elif backend == TTSBackend.ESPEAK_NG.value:
            success = self._speak_espeak(processed, voice_id, rate, pitch, volume)
        elif backend == TTSBackend.PIPER.value:
            success = self._speak_piper(processed, voice_id, rate, pitch, volume)
        else:
            print(f"★ UNKNOWN BACKEND: {backend!r}")
            logger.error("Unknown backend: %s", backend)
            return False

        print(f"★ Backend result: success={success}")
        if success:
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

        # Kill Piper sub-process if active
        piper = getattr(self, "_piper_proc", None)
        if piper:
            try:
                piper.kill()
            except ProcessLookupError:
                pass
            self._piper_proc = None

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
            return self._speak_spd_fallback(text, voice_id, output_module, rate, pitch, volume)

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
                output_module, voice_id, rate, pitch, spd_vol, text[:60],
            )
            print(f"★ speechd SPEAK: module={output_module}, voice={voice_id}, text={text!r}")
            return True

        except Exception as e:
            logger.error("speechd failed: %s", e)
            self._close_spd_client()
            # Fallback to spd-say
            return self._speak_spd_fallback(text, voice_id, output_module, rate, pitch, volume)

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

    def _speak_espeak(
        self,
        text: str,
        voice_id: str,
        rate: int,
        pitch: int,
        volume: int,
    ) -> bool:
        """Speak via espeak-ng directly."""
        cmd = ["espeak-ng"]

        # Extract actual voice name from our ID format
        actual_voice = voice_id.removeprefix("espeak-") if voice_id.startswith("espeak-") else voice_id
        if actual_voice:
            cmd.extend(["-v", actual_voice])

        # espeak rate is in WPM (default 175), speech-dispatcher is -100..100
        wpm = 175 + int(rate * 1.5)
        cmd.extend(["-s", str(max(80, min(450, wpm)))])

        # espeak pitch is 0-99 (default 50)
        esp_pitch = 50 + int(pitch * 0.5)
        cmd.extend(["-p", str(max(0, min(99, esp_pitch)))])

        # espeak volume is 0-200 (default 100)
        # Ensure minimum audible volume (10) to avoid silent output
        esp_vol = max(10, int(volume * 2)) if volume > 0 else 10
        cmd.extend(["-a", str(min(200, esp_vol))])

        # espeak-ng takes text as positional argument
        cmd.append(text)

        print(f"★ espeak-ng CMD: {cmd}")
        result = self._start_process_no_stdin(cmd)
        print(f"★ espeak-ng started: {result}, PID={self._process.pid if self._process else 'None'}")
        return result

    def _speak_piper(self, text: str, voice_id: str, rate: int, pitch: int, volume: int) -> bool:
        """Speak via Piper neural TTS.

        voice_id format: "piper:/absolute/path/to/model.onnx"
        The binary is piper-tts (BigLinux) or piper.

        Rate/Pitch/Volume mapping:
          rate (-100..100) → length_scale: -100=0.3 (fast), 0=1.0, 100=2.5 (slow)
          pitch (-100..100) → noise_scale: maps to voice expressiveness
          volume (0..100) → pactl/amixer volume for playback
        """
        from biglinux_tts.services.voice_manager import _find_piper_binary

        piper_bin = _find_piper_binary()
        if not piper_bin:
            logger.error("Piper binary not found (tried piper-tts, piper)")
            return False

        # Extract model path from voice_id
        model_path = voice_id.removeprefix("piper:") if voice_id.startswith("piper:") else voice_id

        if not os.path.isfile(model_path):
            logger.error("Piper model not found: %s", model_path)
            return False

        # Convert rate (-100..100) to length_scale
        # In Piper: lower length_scale = faster speech
        # UI: rate=-100 (Slow), rate=0 (Normal), rate=100 (Fast)
        # rate=100 (Fast) → 0.3, rate=0 → 1.0, rate=-100 (Slow) → 2.5
        if rate >= 0:
            length_scale = 1.0 - (rate / 100.0) * 0.7  # 1.0 → 0.3
        else:
            length_scale = 1.0 - (rate / 100.0) * 1.5  # 1.0 → 2.5

        # Convert pitch (-100..100) to noise_scale
        # Higher noise_scale = more expressive/varied intonation
        # -100 → 0.1 (flat), 0 → 0.667 (default), 100 → 1.0 (very expressive)
        noise_scale = 0.667 + (pitch / 100.0) * 0.333

        # noise_w: width of phoneme duration noise
        noise_w = 0.8

        # Sentence silence
        sentence_silence = 0.2

        logger.debug(
            "Piper: bin=%s, model=%s, length_scale=%.2f, noise_scale=%.3f",
            piper_bin, model_path, length_scale, noise_scale,
        )

        # Build piper command
        cmd_piper = [
            piper_bin, "--model", model_path, "--output-raw",
            "--length_scale", f"{length_scale:.2f}",
            "--noise_scale", f"{noise_scale:.3f}",
            "--noise_w", f"{noise_w:.2f}",
            "--sentence_silence", f"{sentence_silence:.1f}",
        ]

        # Convert volume (0..100) to aplay-compatible format
        # aplay doesn't have volume control, so we use paplay or
        # pipe through sox for volume adjustment
        # Ensure minimum audible volume (0.2) to avoid silent output
        vol_factor = max(0.2, min(2.0, volume / 50.0)) if volume > 0 else 0.2

        logger.info(
            "Piper: bin=%s, model=%s, vol_factor=%.2f, cmd=%s",
            piper_bin, model_path, vol_factor, cmd_piper,
        )

        try:
            piper_proc = subprocess.Popen(
                cmd_piper,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            if vol_factor != 1.0:
                # Use sox for volume adjustment if available
                sox_available = subprocess.run(
                    ["which", "sox"], capture_output=True, timeout=2,
                ).returncode == 0

                if sox_available:
                    sox_cmd = [
                        "sox", "-t", "raw", "-r", "22050", "-e", "signed-integer",
                        "-b", "16", "-c", "1", "-",
                        "-t", "raw", "-",
                        "vol", f"{vol_factor:.2f}",
                    ]
                    sox_proc = subprocess.Popen(
                        sox_cmd,
                        stdin=piper_proc.stdout,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                    play_cmd = [
                        "aplay", "-r", "22050", "-f", "S16_LE",
                        "-t", "raw", "-q", "-",
                    ]
                    play_proc = subprocess.Popen(
                        play_cmd,
                        stdin=sox_proc.stdout,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._piper_proc = piper_proc
                    self._process = play_proc
                else:
                    # No sox, just play at default volume
                    play_cmd = [
                        "aplay", "-r", "22050", "-f", "S16_LE",
                        "-t", "raw", "-q", "-",
                    ]
                    play_proc = subprocess.Popen(
                        play_cmd,
                        stdin=piper_proc.stdout,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._piper_proc = piper_proc
                    self._process = play_proc
            else:
                # Default volume — direct pipe
                play_cmd = [
                    "aplay", "-r", "22050", "-f", "S16_LE",
                    "-t", "raw", "-q", "-",
                ]
                play_proc = subprocess.Popen(
                    play_cmd,
                    stdin=piper_proc.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._piper_proc = piper_proc
                self._process = play_proc

            # Send text to Piper stdin then close
            if piper_proc.stdin:
                piper_proc.stdin.write(text.encode("utf-8"))
                piper_proc.stdin.close()

            # Store both processes for cleanup
            self._piper_proc = piper_proc
            self._process = play_proc
            print(f"★ Piper STARTED: piper_pid={piper_proc.pid}, play_pid={play_proc.pid}, text={text[:60]!r}")
            return True

        except (FileNotFoundError, OSError) as e:
            logger.error("Failed to start Piper: %s", e)
            return False

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

        Only kills espeak-ng and piper processes (which we launch directly).
        Never kills speech-dispatcher components (sd_rhvoice, spd-say) as
        those are managed by the speech-dispatcher daemon.
        """
        for proc_name in ["espeak-ng", "piper-tts"]:
            try:
                subprocess.run(
                    ["pkill", "-f", proc_name],
                    capture_output=True,
                    timeout=2,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

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
                        logger.debug("TTS stderr: %s", stderr_data.decode(errors="replace").strip())
                except Exception:
                    pass
            if rc != 0:
                logger.warning("TTS process exited with code %d", rc)
            self._process = None
            self._set_state(TTSState.IDLE)
            self._watch_id = 0
            return False  # Stop the timer
        if self._process is None:
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

    def cleanup(self) -> None:
        """Clean up resources on shutdown."""
        self._stop_watch()
        self._close_spd_client()
        if self.is_speaking:
            self.stop()
