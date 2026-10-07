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
from collections import deque
from collections.abc import Callable
from typing import Any

from config import TTSBackend, TTSState
from services.text_processor import process_text
from services.voice_manager import VoiceInfo
from utils.i18n import _
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

# Backends that synthesize before any sound is heard: their requests start in
# LOADING and switch to SPEAKING when playback really begins.
_SYNTHESIZE_FIRST = frozenset({
    TTSBackend.ESPEAK_NG.value,
    TTSBackend.PIPER.value,
    TTSBackend.KOKORO.value,
})

# Product names shown in messages (not translated).
ENGINE_NAMES = {
    TTSBackend.SPEECH_DISPATCHER.value: "Speech Dispatcher",
    TTSBackend.RHVOICE.value: "RHVoice",
    TTSBackend.ESPEAK_NG.value: "espeak-ng",
    TTSBackend.PIPER.value: "Piper",
    TTSBackend.KOKORO.value: "Kokoro",
}


def engine_name(backend: str) -> str:
    return ENGINE_NAMES.get(backend, backend)


def _in_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def _player_missing() -> str:
    return _("Could not play audio. Check that alsa-utils (aplay) is installed.")


# ── Parameter mapping (pure functions — unit-tested) ─────────────────
#
# Semantic convention across ALL backends:
#   rate:   -100 (slowest) … +100 (fastest)
#   pitch:  -100 (lowest)  … +100 (highest)
#   volume:    0 (MUTE)    … 100 (loudest)   — 0 must always be silent.
# Each backend adapts these to its own native parameters below.


def espeak_wpm(rate: int) -> int:
    """UI rate (-100..100) → espeak words-per-minute (80..450, higher=faster)."""
    return max(80, min(450, 175 + int(rate * 1.5)))


def espeak_pitch(pitch: int) -> int:
    """UI pitch (-100..100) → espeak pitch (0..99, 50=normal)."""
    return max(0, min(99, 50 + int(pitch * 0.5)))


def espeak_volume(volume: int) -> int:
    """UI volume (0..100) → espeak amplitude (0..200). 0 == true mute."""
    if volume <= 0:
        return 0
    return min(200, max(10, int(volume * 2)))


def piper_length_scale(rate: int) -> float:
    """UI rate (-100..100) → Piper length_scale (smaller=faster).

    rate=+100 → 0.30 (fast), rate=0 → 1.0, rate=-100 → 2.50 (slow).
    """
    if rate >= 0:
        return 1.0 - (rate / 100.0) * 0.7
    return 1.0 - (rate / 100.0) * 1.5


def piper_noise_scale(pitch: int) -> float:
    """UI pitch (-100..100) → Piper noise_scale (voice expressiveness proxy).

    Note: Piper has no true pitch control; this maps to noise_scale. The UI
    labels this as "expressiveness" for Piper rather than pitch.
    """
    return 0.667 + (pitch / 100.0) * 0.333


def volume_factor(volume: int) -> float:
    """UI volume (0..100) → linear gain factor. 0 == true mute (0.0)."""
    if volume <= 0:
        return 0.0
    return max(0.2, min(2.0, volume / 50.0))


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
        # Monotonic request generation. Incremented on every speak()/stop().
        # Background synthesis captures the generation at dispatch and refuses
        # to start playback if a newer request has since arrived — this prevents
        # stale audio from a previous Alt+V starting after a newer one.
        self._generation: int = 0
        self._dispatch_gen: int = 0
        # Pause/resume: SIGSTOP/SIGCONT the audio player process. The player
        # (aplay) keeps poll()==None while stopped, so state stays SPEAKING and
        # the watch keeps running — playback simply freezes and resumes in place.
        self._paused: bool = False
        # Why the last request failed: a translated sentence that says how to
        # fix it, plus optional technical detail (engine stderr). Kept until
        # the next speak()/stop() so the UI can show it.
        self._error_message: str = ""
        self._error_detail: str = ""
        # What the UI can offer to fix it: "voice-manager", "retry" or "".
        self._error_action: str = ""
        # Backend of the request being handled (for error messages).
        self._active_backend: str = ""
        # Set when audio for the current request has actually started.
        self._audio_started: bool = False
        # True once text went through speech-dispatcher in this session. Only
        # then does stop() talk to the daemon: any SSIP/spd-say call starts
        # speech-dispatcher, and starting it can run every installed module.
        self._spd_used: bool = False
        # The last transition to IDLE came from stop() during speech.
        self._stopped_by_user: bool = False
        # Last lines a streaming engine wrote to stderr (error detail).
        self._stderr_tail: deque[str] = deque(maxlen=12)

    def _is_current(self, gen: int) -> bool:
        """True if `gen` is still the active request generation."""
        return gen == self._generation

    @property
    def state(self) -> TTSState:
        """Current TTS state."""
        return self._state

    @property
    def is_speaking(self) -> bool:
        """Whether a request is in progress (loading the voice or speaking).

        This is what the shortcut toggles on: pressing it again while the voice
        loads cancels the request just like it stops speech.
        """
        return self._state in (TTSState.LOADING, TTSState.SPEAKING)

    @property
    def last_error(self) -> str:
        """Translated reason of the last failure ("" if none)."""
        return self._error_message

    @property
    def last_error_action(self) -> str:
        """Suggested recovery: "voice-manager", "retry" or ""."""
        return self._error_action

    @property
    def last_error_detail(self) -> str:
        """Technical detail of the last failure (engine output), may be ""."""
        return self._error_detail

    @property
    def stopped_by_user(self) -> bool:
        """True if the current IDLE state was reached through stop()."""
        return self._stopped_by_user and self._state == TTSState.IDLE

    @property
    def is_paused(self) -> bool:
        """Whether playback is currently paused (SIGSTOP'd)."""
        return self._paused and self.is_speaking

    def _audio_procs(self) -> list[subprocess.Popen]:
        """Live audio/synthesis subprocesses that can be paused as a group."""
        procs = []
        for attr in ("_process", "_rh_proc", "_piper_proc", "_kokoro_proc"):
            p = getattr(self, attr, None)
            if p is not None and p.poll() is None:
                procs.append(p)
        return procs

    def pause(self) -> bool:
        """Freeze playback in place (SIGSTOP). Returns True if it paused."""
        if self._paused or not self.is_speaking:
            return False
        procs = self._audio_procs()
        if not procs:
            return False
        for p in procs:
            try:
                p.send_signal(signal.SIGSTOP)
            except (ProcessLookupError, OSError):
                pass
        self._paused = True
        logger.debug("Speech paused")
        return True

    def resume(self) -> bool:
        """Resume playback from where it was paused (SIGCONT)."""
        if not self._paused:
            return False
        for p in self._audio_procs():
            try:
                p.send_signal(signal.SIGCONT)
            except (ProcessLookupError, OSError):
                pass
        self._paused = False
        logger.debug("Speech resumed")
        return True

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
        normalize_numbers: bool = True,
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

        self._error_message = ""
        self._error_detail = ""
        self._error_action = ""

        if stop_previous:
            # Always stop any previous speech (even if state tracking says idle,
            # a background thread might still be alive between chunks).
            # NOTE: no sleep here — speak() may run on the GTK main thread, and a
            # blocking sleep would freeze the UI. stop() already cancels speechd
            # (SSIP cancel + close + `spd-say -C`) and terminates our processes.
            self.stop()

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
            normalize_numbers=normalize_numbers,
        )

        if not processed:
            logger.debug("Text is empty after processing")
            return False

        logger.debug("Processed text: %r", processed[:80])

        # Capture the current request generation for background synths. In
        # interrupt mode stop() (above) already bumped it, so stale synths from
        # the previous request abort. In simultaneous mode (stop_previous=False)
        # the generation is unchanged, so concurrent speeches coexist and only
        # an explicit stop() invalidates them.
        self._dispatch_gen = self._generation
        self._active_backend = backend
        self._audio_started = False
        self._stderr_tail.clear()

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
            success = False

        if success:
            self._paused = False
            self._stopped_by_user = False
            self._last_spoken_text = processed
            self._last_backend = backend
            self._last_voice_id = voice_id
            if self._audio_started or backend not in _SYNTHESIZE_FIRST:
                self._set_state(TTSState.SPEAKING)
            else:
                self._set_state(TTSState.LOADING)
            self._start_watch()
            if self._on_progress:
                self._on_progress(processed[:100])
        else:
            if not self._error_message:
                self._error_message = _("{engine} could not start. Check that it is installed.").format(
                    engine=engine_name(backend)
                )
            if not self._error_action:
                self._error_action = "retry"
            self._set_state(TTSState.ERROR)

        return success

    def stop(self) -> None:
        """Stop current speech immediately."""
        was_busy = self._state in (TTSState.LOADING, TTSState.SPEAKING)
        # Invalidate any in-flight background synthesis so it won't start audio.
        self._generation += 1
        self._stop_watch()
        self._error_message = ""
        self._error_detail = ""

        # If paused, resume the frozen players first so SIGTERM is delivered
        # promptly (a SIGSTOP'd process ignores SIGTERM until continued).
        if self._paused:
            for p in self._audio_procs():
                try:
                    p.send_signal(signal.SIGCONT)
                except (ProcessLookupError, OSError):
                    pass
            self._paused = False

        # Stop speech-dispatcher via SSIP API
        if self._spd_client:
            try:
                self._spd_client.cancel()
            except Exception:
                pass
            self._close_spd_client()

        # Cancel speech-dispatcher queue via CLI (fallback) — only if this app
        # actually used speech-dispatcher. Otherwise spd-say would start the
        # daemon (socket activation) on every stop, which can make other
        # modules speak and blocks the UI for up to 2 s.
        if self._spd_used:
            try:
                subprocess.run(
                    ["spd-say", "-C"],
                    capture_output=True,
                    timeout=2,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        # Kill running process. stop() may run on the GTK main thread, so keep
        # the reap bounded and short — aplay/spd-say die immediately on SIGTERM.
        if self._process:
            try:
                self._process.send_signal(signal.SIGTERM)
                self._process.wait(timeout=0.5)
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

        self._stopped_by_user = was_busy
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
        if volume <= 0:
            return True  # true mute — nothing audible

        self._spd_used = True
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

            client.speak(
                text, callback=on_end,
                # Default event types include BEGIN, which would end the
                # request as soon as speech starts.
                event_types=(speechd.CallbackType.END, speechd.CallbackType.CANCEL),
            )

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

                    client.speak(
                        text, callback=on_end2,
                        event_types=(speechd.CallbackType.END, speechd.CallbackType.CANCEL),
                    )
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
        self._spd_used = True
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
        if volume <= 0:
            return True  # true mute — nothing audible
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
            # aplay owns the read end now; keeping our copy open leaks the fd
            # and would keep RHVoice-test from seeing a closed pipe.
            if rh_proc.stdout:
                rh_proc.stdout.close()

            # Send text to RHVoice-test stdin
            if rh_proc.stdin:
                rh_proc.stdin.write(text.encode("utf-8"))
                rh_proc.stdin.close()

            # The standard process tracker will watch play_proc
            self._process = play_proc
            self._rh_proc = rh_proc # Keep ref to kill if needed
            return True

        except FileNotFoundError:
            self._error_message = _("RHVoice is not installed. Install the rhvoice package.")
            return False
        except OSError as e:
            logger.error("Failed to start RHVoice native: %s", e)
            return False

    def _default_espeak_voice(self) -> str:
        """System-locale espeak voice, never a silent English default.

        Avoids the "speaks English" surprise when no voice is configured by
        falling back to the system language rather than espeak's built-in en.
        """
        import locale

        try:
            loc = (locale.getlocale()[0] or locale.getdefaultlocale()[0] or "")
        except (ValueError, IndexError):
            loc = ""
        loc = loc.lower()
        if loc.startswith("pt"):
            return "pt-br" if "br" in loc else "pt"
        if "_" in loc:
            return loc.split("_", 1)[0]
        return loc or "pt-br"

    def _speak_espeak(
        self,
        text: str,
        voice_id: str,
        rate: int,
        pitch: int,
        volume: int,
    ) -> bool:
        """Speak via espeak-ng (native FFI or subprocess fallback).

        Runs off the GTK main thread: native synthesis + playback are done in a
        background thread so the UI never blocks for the speech duration.
        volume == 0 is a true mute (no audible output).
        """
        actual_voice = (
            voice_id.removeprefix("espeak-")
            if voice_id.startswith("espeak-")
            else voice_id
        ) or self._default_espeak_voice()

        wpm = espeak_wpm(rate)
        esp_pitch = espeak_pitch(pitch)
        esp_vol = espeak_volume(volume)

        # True mute: nothing audible, but register the request so state/history
        # remain consistent.
        if esp_vol <= 0:
            self._bg_thread = None
            return True

        engine = _get_tts_engine()
        gen = self._dispatch_gen

        def _generate_and_play() -> None:
            # Native path: synthesize to WAV (audio-free) then play via aplay,
            # so the process is cancellable and never blocks the main thread.
            if engine and hasattr(engine, "synthesize_espeak"):
                try:
                    wav_bytes = engine.synthesize_espeak(
                        text, actual_voice, wpm, esp_pitch, esp_vol,
                    )
                    if not self._is_current(gen):
                        return  # superseded — discard stale audio
                    if wav_bytes and len(wav_bytes) > 100:
                        self._play_wav_bytes(wav_bytes)
                        return
                except Exception as e:
                    logger.warning("Native espeak synth failed, falling back: %s", e)

            # Subprocess fallback: espeak-ng writes WAV to a temp file, we play it.
            import tempfile

            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp_path = tmp.name
            tmp.close()
            cmd = [
                "espeak-ng", "-v", actual_voice,
                "-s", str(wpm), "-p", str(esp_pitch), "-a", str(esp_vol),
                "-w", tmp_path, text,
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=120)
                if not self._is_current(gen):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    return  # superseded — discard stale audio
                if proc.returncode == 0 and os.path.getsize(tmp_path) > 100:
                    play_proc = subprocess.Popen(
                        ["aplay", "-q", tmp_path],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    self._piper_tmp_path = tmp_path
                    self._begin_playback(play_proc)
                    return
                detail = proc.stderr.decode(errors="replace").strip()
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
                logger.error("espeak-ng subprocess failed: %s", e)
                detail = str(e)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            if self._is_current(gen):
                self._fail(_("espeak-ng could not read this text. Check that espeak-ng is installed."), detail)

        thread = threading.Thread(target=_generate_and_play, daemon=True)
        self._bg_thread = thread
        thread.start()
        return True

    def _play_wav_bytes(self, wav_bytes: bytes) -> None:
        """Write WAV bytes to a temp file and play via aplay (cancellable)."""
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.write(wav_bytes)
        tmp.close()
        try:
            play_proc = subprocess.Popen(
                ["aplay", "-q", tmp_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._piper_tmp_path = tmp_path
            self._begin_playback(play_proc)
        except (FileNotFoundError, OSError) as e:
            logger.error("aplay failed: %s", e)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            self._fail(_player_missing(), str(e))

    def _stream_piper(
        self, chunks: list[str], model_path: str, length_scale: float,
        noise_scale: float, noise_w: float, vol_factor: float, gen: int, engine,
    ) -> None:
        """Stream Piper synthesis: synth chunk N+1 while chunk N plays.

        TTFA equals the time to synthesize the first chunk, not the whole text.
        Runs in a background thread; honors the generation race-guard so a newer
        speak()/stop() aborts it. History is saved text-only for streamed speech.
        """
        import tempfile
        import time

        from services.voice_manager import _find_piper_binary

        sox_available = shutil.which("sox") is not None
        piper_bin = None if engine else _find_piper_binary()

        def _synth(chunk: str) -> str | None:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            path = tmp.name
            tmp.close()
            if engine:
                try:
                    wav = engine.synthesize_piper(
                        chunk, model_path, length_scale, noise_scale, noise_w, vol_factor,
                    )
                    if wav and len(wav) >= 100:
                        with open(path, "wb") as f:
                            f.write(wav)
                        return path
                except Exception as e:
                    logger.debug("stream native synth failed: %s", e)
            if piper_bin:
                try:
                    p = subprocess.Popen(
                        [piper_bin, "--model", model_path, "--output_file", path,
                         "--length_scale", f"{length_scale:.2f}",
                         "--noise_scale", f"{noise_scale:.3f}",
                         "--noise_w", f"{noise_w:.2f}", "--sentence_silence", "0.05"],
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._piper_proc = p
                    if p.stdin:
                        p.stdin.write(chunk.encode("utf-8"))
                        p.stdin.close()
                    p.wait()
                    self._piper_proc = None
                    if p.returncode == 0 and os.path.getsize(path) >= 100:
                        return path
                except (OSError, FileNotFoundError) as e:
                    logger.debug("stream subprocess synth failed: %s", e)
            try:
                os.unlink(path)
            except OSError:
                pass
            return None

        def _play(path: str) -> subprocess.Popen | None:
            if not engine and vol_factor != 1.0 and sox_available:
                cmd = ["play", "-q", path, "vol", f"{vol_factor:.2f}"]
            else:
                cmd = ["aplay", "-q", path]
            try:
                return subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except (OSError, FileNotFoundError):
                return None

        def _wait(proc: subprocess.Popen) -> bool:
            """Wait for playback; return False if superseded/stopped."""
            while proc.poll() is None:
                if not self._is_current(gen):
                    proc.terminate()
                    return False
                time.sleep(0.03)
            # The player may have ended because stop() killed it: only report
            # success if this request is still current, otherwise the caller
            # would go on to start the next chunk after the user hit Stop.
            return self._is_current(gen)

        play_proc: subprocess.Popen | None = None
        cur = _synth(chunks[0])  # first synth defines TTFA
        temps: list[str] = []
        try:
            i = 0
            while i < len(chunks):
                if not self._is_current(gen):
                    break
                if cur is None:  # this chunk failed; try the next
                    cur = _synth(chunks[i + 1]) if i + 1 < len(chunks) else None
                    i += 1
                    continue
                if play_proc is not None and not _wait(play_proc):
                    break
                if not self._is_current(gen):  # stop()/newer speak() arrived
                    break
                temps.append(cur)
                play_proc = _play(cur)
                if play_proc is not None:
                    self._begin_playback(play_proc)
                # Prefetch the next chunk while the current one plays.
                nxt = _synth(chunks[i + 1]) if i + 1 < len(chunks) else None
                i += 1
                cur = nxt
            if play_proc is not None:
                _wait(play_proc)
        finally:
            for p in temps:
                try:
                    os.unlink(p)
                except OSError:
                    pass
            if cur:
                try:
                    os.unlink(cur)
                except OSError:
                    pass
            if self._is_current(gen):
                if not temps:
                    self._fail(_("Piper could not read this text. Try another voice."))
                else:
                    self._maybe_save_history(None)  # text-only history for streams

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
            self._error_message = _("The selected Piper voice is not installed. Choose another voice in the Voice Manager.")
            self._error_action = "voice-manager"
            return False

        length_scale = piper_length_scale(rate)
        noise_scale = piper_noise_scale(pitch)
        noise_w = 0.8

        # Volume factor — 0 == true mute (0.0). The native engine renders a
        # silent WAV at 0.0; the subprocess path skips playback when muted.
        vol_factor = volume_factor(volume)

        engine = _get_tts_engine()
        gen = self._dispatch_gen

        # Streaming path for long text: synthesize + play sentence chunks with
        # prefetch, so audio starts after the FIRST chunk (~0.25 s) instead of
        # after the whole text. Short text keeps the single-shot path below
        # (already fast, and it preserves per-entry audio history).
        from services.text_processor import chunk_text

        if len(text) > 600 and vol_factor > 0.0:
            chunks = chunk_text(text, max_chars=600)
            if len(chunks) > 1:
                thread = threading.Thread(
                    target=self._stream_piper,
                    args=(chunks, model_path, length_scale, noise_scale,
                          noise_w, vol_factor, gen, engine),
                    daemon=True,
                )
                self._bg_thread = thread
                thread.start()
                return True

        # Create temp file for audio
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()

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
                    if self._is_current(gen):
                        self._fail(_("Piper could not read this text. Try another voice."))
                    return

                # Race guard: a newer speak()/stop() arrived while we were
                # synthesizing — discard this (stale) audio, never play it.
                if not self._is_current(gen):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    return

                # True mute: volume 0 → nothing audible. Skip playback entirely.
                if vol_factor <= 0.0:
                    self._piper_proc = None
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    return

                # Phase 2: play the pre-generated audio
                # Native already applied volume, subprocess needs sox
                if not engine and vol_factor != 1.0:
                    sox_available = shutil.which("sox") is not None
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
                self._piper_tmp_path = tmp_path
                self._begin_playback(play_proc)

            except (FileNotFoundError, OSError) as e:
                logger.error("Failed to start Piper: %s", e)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                self._fail(_player_missing(), str(e))

        thread = threading.Thread(target=_generate_and_play, daemon=True)
        self._bg_thread = thread
        thread.start()
        return True

    def _speak_kokoro(
        self, text: str, voice_id: str, rate: int, pitch: int, volume: int
    ) -> bool:
        """Speak via Kokoro neural TTS.

        Tries Python kokoro library first (KPipeline), falls back to koko binary
        (biglinux-kokoro-tts package) if Python lib is not installed.
        """
        import tempfile

        try:
            from kokoro import KPipeline
            import soundfile as sf
        except ImportError:
            # Python kokoro not available — fallback to koko binary
            return self._speak_kokoro_koko(text, voice_id, rate, pitch, volume)

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

        from services.kokoro_voice_service import kokoro_speed

        emotion = kokoro_cfg.emotion_preset if kokoro_cfg else "neutral"
        speed = kokoro_speed(rate, emotion)

        # Volume factor — 0 == true mute (0.0)
        vol_factor = volume_factor(volume)

        logger.debug(
            "Kokoro: voice=%s, lang=%s, speed=%.2f, "
            "emotion=%s, vol=%.2f, text=%r",
            kokoro_voice, lang_code, speed,
            emotion, vol_factor, text[:60],
        )

        def _pipeline():
            # Built in the worker thread: creating a KPipeline loads PyTorch
            # and the model (seconds) and must never block the GTK main loop.
            cached_lang = getattr(self, "_kokoro_cached_lang", None)
            if cached_lang != lang_code or not hasattr(self, "_kokoro_api_pipeline"):
                self._kokoro_api_pipeline = KPipeline(lang_code=lang_code)
                self._kokoro_cached_lang = lang_code
            return self._kokoro_api_pipeline

        # Check sox availability for volume control
        sox_available = shutil.which("sox") is not None

        def _build_play_cmd(wav_path: str) -> list[str] | None:
            if vol_factor <= 0.0:
                return None  # true mute — no playback
            if vol_factor != 1.0 and sox_available:
                return ["play", "-q", wav_path, "vol", f"{vol_factor:.2f}"]
            return ["aplay", "-q", wav_path]

        def _generate_and_play() -> None:
            """Generate audio via KPipeline and play chunks sequentially."""
            tmp_paths: list[str] = []
            play_proc: subprocess.Popen | None = None

            gen = self._dispatch_gen
            try:
                generator = _pipeline()(text, voice=kokoro_voice, speed=speed)

                for _gs, _ps, audio in generator:
                    if self._kokoro_stop_event.is_set() or not self._is_current(gen):
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

                    # Start playing this chunk (skip when muted)
                    play_cmd = _build_play_cmd(path)
                    if play_cmd is None:
                        continue
                    play_proc = subprocess.Popen(
                        play_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._begin_playback(play_proc)

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
                if self._is_current(gen):
                    self._fail(_("Kokoro could not read this text. Try another voice."), str(e))
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

    def _speak_kokoro_koko(
        self, text: str, voice_id: str, rate: int, pitch: int, volume: int
    ) -> bool:
        """Speak via the koko binary (biglinux-kokoro-tts package).

        Fallback when the Python kokoro library is not installed — the normal
        case on BigLinux. ``koko pipe`` reads the text from stdin, splits it
        into sentences and streams each one to the speaker as soon as it is
        synthesized. The command is built by kokoro_voice_service, the same
        code the Voice Manager preview uses.
        """
        if volume <= 0:
            return True  # true mute — nothing audible

        from services.kokoro_voice_service import (
            KOKO_AUDIO_STARTED_MARKER,
            build_koko_command,
            koko_problem,
            koko_workdir,
            kokoro_speed,
        )

        kokoro_cfg = self._settings.speech.kokoro if self._settings else None
        blend = kokoro_cfg.voice_blend if kokoro_cfg else ""
        problem = koko_problem(voice_id, blend)
        if problem:
            logger.error("Kokoro unavailable: %s", problem)
            self._error_message = problem
            self._error_action = "voice-manager"
            return False

        speed = kokoro_speed(rate, kokoro_cfg.emotion_preset if kokoro_cfg else "neutral")
        cmd = build_koko_command(
            voice_id, speed=speed, blend=blend,
            blend_ratio=kokoro_cfg.blend_ratio if kokoro_cfg else 0.5,
        )
        logger.info("Kokoro (koko binary): voice=%s, lang=%s, speed=%.2f", cmd[cmd.index("-s") + 1], cmd[cmd.index("-l") + 1], speed)
        return self._start_process(
            cmd, text, cwd=koko_workdir(), audio_marker=KOKO_AUDIO_STARTED_MARKER,
        )

    def _start_process(
        self,
        cmd: list[str],
        text: str,
        cwd: str | None = None,
        audio_marker: str | None = None,
    ) -> bool:
        """Start a TTS process with text piped to stdin.

        With ``audio_marker``, the process both synthesizes and plays: its
        stderr is read in a thread, the request moves from LOADING to SPEAKING
        when the marker appears, and the last lines are kept as error detail.
        """
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE if audio_marker else subprocess.DEVNULL,
                cwd=cwd,
            )
            if proc.stdin:
                try:
                    proc.stdin.write(text.encode("utf-8"))
                    proc.stdin.close()
                except BrokenPipeError:
                    pass  # exited early; its exit status reports why

            self._process = proc
            if audio_marker and proc.stderr is not None:
                gen = self._dispatch_gen
                threading.Thread(
                    target=self._read_engine_stderr,
                    args=(proc, audio_marker, gen),
                    daemon=True,
                ).start()
            else:
                self._audio_started = True
            return True

        except FileNotFoundError:
            logger.error("Command not found: %s", cmd[0])
            return False
        except OSError as e:
            logger.error("Failed to start TTS: %s", e)
            return False

    def _read_engine_stderr(self, proc: subprocess.Popen, marker: str, gen: int) -> None:
        """Follow a streaming engine's stderr (worker thread)."""
        stream = proc.stderr
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if marker in line:
                    if self._is_current(gen) and proc is self._process:
                        self._mark_audio_started()
                else:
                    self._stderr_tail.append(line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _begin_playback(self, proc: subprocess.Popen) -> None:
        """Track ``proc`` as the audio player: sound is now being produced."""
        self._process = proc
        self._mark_audio_started()

    def _mark_audio_started(self) -> None:
        self._audio_started = True
        if self._state == TTSState.LOADING:
            self._set_state(TTSState.SPEAKING)

    def _fail(self, message: str, detail: str = "", action: str = "retry") -> None:
        """Report a failure the person can act on (any thread)."""
        self._error_action = action
        self._error_message = message
        self._error_detail = (detail or "").strip()[-600:]
        logger.warning("TTS error: %s %s", message, self._error_detail)
        self._set_state(TTSState.ERROR)

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
            # RHVoice-test | aplay: aplay ends cleanly on EOF even when the
            # synthesizer failed, so check the synthesizer too.
            rh = getattr(self, "_rh_proc", None)
            if rc == 0 and rh is not None:
                try:
                    rh.wait(timeout=0.2)
                    rc = rh.returncode or 0
                except subprocess.TimeoutExpired:
                    pass
                self._rh_proc = None
            self._process = None
            # A positive status is the engine reporting a failure; a negative
            # one means it was killed by a signal (stop/supersede), not an error.
            if rc > 0 and self._state != TTSState.ERROR:
                self._watch_id = 0
                self._fail(
                    _("{engine} stopped with an error and could not read the text.").format(
                        engine=engine_name(self._active_backend)
                    ),
                    "\n".join(self._stderr_tail),
                )
                return False

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
            self._finish_request()
            return False  # Stop the timer
        if self._process is None:
            # Background thread may still be generating audio (no process yet)
            if self._has_active_bg_thread():
                return True  # Keep polling — generation in progress
            self._finish_request()
            return False
        return True  # Keep polling

    def _finish_request(self) -> None:
        """Playback ended: back to IDLE unless a failure is being shown."""
        self._watch_id = 0
        if self._state != TTSState.ERROR:
            self._set_state(TTSState.IDLE)

    def _set_state(self, state: TTSState) -> None:
        """Update state and notify listeners.

        The state itself changes immediately (from any thread). Listeners are
        always called on the GTK main thread: worker threads finish synthesis
        and must not touch widgets, the tray or D-Bus directly.
        """
        if state == self._state:
            return
        old = self._state
        self._state = state
        logger.debug("TTS state: %s → %s", old, state)
        if _in_main_thread():
            self._notify_state(state)
            return
        try:
            from gi.repository import GLib

            def _deliver() -> bool:
                # Drop it if the state moved on meanwhile (e.g. a late SPEAKING
                # from a worker after the main thread already reported IDLE).
                if self._state == state:
                    self._notify_state(state)
                return False

            GLib.idle_add(_deliver)
        except ImportError:
            self._notify_state(state)

    def _notify_state(self, state: TTSState) -> None:
        if self._on_state_changed:
            try:
                self._on_state_changed(state)
            except Exception as e:
                logger.warning("State change listener error: %s", e)
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
                max_entries=getattr(history, "max_entries", 0),
                max_age_days=getattr(history, "max_age_days", 0),
            )
        except Exception as e:
            logger.error("Failed to save history: %s", e)

    _prewarmed_model: str = ""

    def prewarm(self, backend: str, voice_id: str) -> None:
        """Preload the selected neural model in a background thread (no audio).

        Reduces the first Alt+V TTFA from ~1.0 s (cold model load) to ~0.06 s.
        Safe to call repeatedly — it is a no-op if the model is already warm or
        the backend has no preloadable model. NEVER produces sound.
        """
        if backend != TTSBackend.PIPER.value:
            return
        model_path = voice_id.removeprefix("piper:") if voice_id.startswith("piper:") else voice_id
        if not model_path or not os.path.isfile(model_path):
            return
        if self._prewarmed_model == model_path:
            return
        engine = _get_tts_engine()
        if not engine or not hasattr(engine, "load_piper"):
            return

        def _load() -> None:
            try:
                engine.load_piper(model_path)
                self._prewarmed_model = model_path
                logger.debug("Piper model prewarmed: %s", model_path)
            except Exception as e:
                logger.debug("Prewarm failed (non-fatal): %s", e)

        threading.Thread(target=_load, daemon=True).start()

    def cleanup(self) -> None:
        """Clean up resources on shutdown."""
        self._stop_watch()
        self._close_spd_client()
        if self.is_speaking:
            self.stop()
