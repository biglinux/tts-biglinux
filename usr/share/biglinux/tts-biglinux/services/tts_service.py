"""
TTS service — speak and stop text with RHVoice, espeak-ng, Piper and Kokoro.

State machine: IDLE → LOADING → SPEAKING → IDLE (or ERROR, kept until the
next request). Handles the speak/stop toggle of the global shortcut.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from config import TTSBackend, TTSState
from services.text_processor import chunk_text, get_system_language, limit_text, process_text
from services.voice_manager import VoiceInfo
from utils.i18n import _

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

# Watch interval in ms for process completion
_WATCH_INTERVAL_MS = 300

# Longer texts are read in chunks of about this size by the engines that
# synthesize before playing (espeak-ng, Piper): sound starts after the first
# chunk, memory stays bounded and Stop takes effect between chunks.
_STREAM_CHUNK_CHARS = 600

# koko writes the text it phonemizes to stderr: never keep that (it would end
# up in error details and logs).
_KOKO_TEXT_LINES = ("CALLING PHONEMIZE ON:", "phonemes:")

# Backends that synthesize before any sound is heard: their requests start in
# LOADING and switch to SPEAKING when playback really begins.
_SYNTHESIZE_FIRST = frozenset({
    TTSBackend.ESPEAK_NG.value,
    TTSBackend.PIPER.value,
    TTSBackend.KOKORO.value,
})

# Product names shown in messages (not translated).
ENGINE_NAMES = {
    TTSBackend.RHVOICE.value: "RHVoice",
    TTSBackend.ESPEAK_NG.value: "espeak-ng",
    TTSBackend.PIPER.value: "Piper",
    TTSBackend.KOKORO.value: "Kokoro",
}


def engine_name(backend: str) -> str:
    return ENGINE_NAMES.get(backend, backend)


def _in_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


# Exit by one of these signals means the engine crashed (e.g. a Rust panic
# aborts with SIGABRT). Stop/supersede use SIGTERM/SIGKILL, which are not errors.
_CRASH_SIGNALS = frozenset({
    signal.SIGABRT, signal.SIGSEGV, signal.SIGBUS, signal.SIGFPE, signal.SIGILL,
})


def _feed_stdin(proc: subprocess.Popen, data: bytes) -> None:
    """Write ``data`` to the process and close its stdin (worker thread)."""
    try:
        proc.stdin.write(data)
    except (BrokenPipeError, OSError, ValueError):
        pass  # exited early or was stopped; its exit status reports why
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass


def _unlink(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        pass


def _temp_wav() -> str:
    """A new private temporary .wav path (mode 0600, random name)."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="biglinux-tts-")
    os.close(fd)
    return path


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
    """Speak/stop lifecycle for every backend."""

    def __init__(self, settings=None) -> None:
        self._state: TTSState = TTSState.IDLE
        self._process: subprocess.Popen[bytes] | None = None
        self._on_state_changed: OnStateChanged | None = None
        self._on_state_changed_extra: list[OnStateChanged] = []
        self._watch_id: int = 0
        self._rh_proc: subprocess.Popen[bytes] | None = None  # RHVoice-test feeding aplay
        self._piper_proc: subprocess.Popen[bytes] | None = None  # piper CLI fallback
        self._piper_tmp_path: str | None = None  # WAV being played (kept for history)
        # Players of earlier readings still running in "simultaneous" mode:
        # Stop and Pause must reach them too.
        self._overlapped: list[tuple[subprocess.Popen, str | None]] = []
        self._last_text: str = ""  # what was asked to be read (for history/replay)
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
        # Identity of each speak() call. In simultaneous mode the generation
        # does not change between readings; this tells a worker whether its
        # player belongs to the newest reading or to an earlier one.
        self._request_id: int = 0
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
    def last_text(self) -> str:
        """The text of the last reading, as it was given (before processing)."""
        return self._last_text

    @property
    def is_paused(self) -> bool:
        """Whether playback is currently paused (SIGSTOP'd)."""
        return self._paused and self.is_speaking

    def _audio_procs(self) -> list[subprocess.Popen]:
        """Live audio/synthesis subprocesses that can be paused as a group."""
        procs = []
        overlapped = [p for p, _path in self._overlapped]
        for p in (self._process, self._rh_proc, self._piper_proc, *overlapped):
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

    def speak(
        self,
        text: str,
        *,
        voice: VoiceInfo | None = None,
        rate: int = -25,
        pitch: int = -25,
        volume: int = 75,
        backend: str = TTSBackend.RHVOICE.value,
        voice_id: str = "",
        language: str | None = None,
        expand_abbreviations: bool = True,
        process_special_chars: bool = True,
        process_urls: bool = False,
        strip_formatting: bool = True,
        normalize_numbers: bool = True,
        max_chars: int = 0,
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
            voice_id: Voice identifier.
            language: Language of the voice (text rules follow it; default:
                the system language).
            expand_abbreviations: Expand common abbreviations.
            process_special_chars: Read special chars aloud.
            process_urls: Read URLs aloud.
            strip_formatting: Remove markdown/HTML.
            max_chars: Read at most this many characters (0 = no limit),
                cut at a sentence or word boundary.
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
            # No sleep here: speak() may run on the GTK main thread.
            self.stop()
        else:
            self._overlap_current()

        # Resolve voice parameters
        if voice:
            voice_id = voice.voice_id
            backend = voice.backend
            language = voice.language or language

        if max_chars > 0:
            text = limit_text(text, max_chars)
        processed = process_text(
            text,
            expand_abbreviations=expand_abbreviations,
            process_special_chars=process_special_chars,
            process_urls=process_urls,
            strip_formatting=strip_formatting,
            normalize_numbers=normalize_numbers,
            language=language,
        )

        if not processed:
            logger.debug("Text is empty after processing")
            return False

        logger.debug("Processed text: %d chars", len(processed))

        # Capture the current request generation for background synths. In
        # interrupt mode stop() (above) already bumped it, so stale synths from
        # the previous request abort. In simultaneous mode (stop_previous=False)
        # the generation is unchanged, so concurrent speeches coexist and only
        # an explicit stop() invalidates them.
        self._dispatch_gen = self._generation
        self._request_id += 1
        self._active_backend = backend
        self._audio_started = False
        self._stderr_tail.clear()

        # Speak via appropriate backend
        if backend == TTSBackend.RHVOICE.value:
            success = self._speak_rhvoice(processed, voice_id, rate, pitch, volume)
        elif backend == TTSBackend.ESPEAK_NG.value:
            success = self._speak_espeak(processed, voice_id, rate, pitch, volume)
        elif backend == TTSBackend.PIPER.value:
            success = self._speak_piper(processed, voice_id, rate, pitch, volume)
        elif backend == TTSBackend.KOKORO.value:
            success = self._speak_kokoro_koko(processed, voice_id, rate, pitch, volume)
        else:
            logger.error("Unknown backend: %s", backend)
            success = False

        if success:
            self._paused = False
            self._stopped_by_user = False
            self._last_spoken_text = processed
            self._last_text = text
            self._last_backend = backend
            self._last_voice_id = voice_id
            if self._audio_started or backend not in _SYNTHESIZE_FIRST:
                self._set_state(TTSState.SPEAKING)
            else:
                self._set_state(TTSState.LOADING)
            self._start_watch()
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

        # Terminate every process of the request (and of overlapped earlier
        # readings). stop() may run on the GTK main thread: the reap is
        # bounded and short — aplay, RHVoice-test, piper and koko die at once.
        procs = [self._process, self._rh_proc, self._piper_proc]
        procs += [p for p, _path in self._overlapped]
        files = [self._piper_tmp_path] + [path for _p, path in self._overlapped]
        self._process = self._rh_proc = self._piper_proc = self._piper_tmp_path = None
        self._overlapped = []
        for proc in procs:
            if proc is None:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass

        for path in files:
            _unlink(path)

        # The worker of the stopped request sees the new generation and exits
        # at its next check; it is not joined here (that could block the UI).
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

    def _speak_rhvoice(
        self,
        text: str,
        voice_id: str,
        rate: int,
        pitch: int,
        volume: int,
    ) -> bool:
        """Speak via RHVoice-test piped into aplay (no speech-dispatcher)."""
        if volume <= 0:
            return True  # true mute — nothing audible
        # RHVoice rate/pitch are percentages (100 = normal); ours are -100..100.
        cmd = ["RHVoice-test"]
        if voice_id:
            cmd.extend(["-p", voice_id])
        cmd.extend([
            "-r", str(max(20, min(300, 100 + rate))),
            "-t", str(max(20, min(200, 100 + pitch))),
            "-v", str(volume),
            "-o", "/dev/stdout",
        ])
        logger.debug("RHVoice direct cmd: %s | aplay", cmd)
        try:
            rh_proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self._error_message = _("RHVoice is not installed. Install the rhvoice package.")
            return False
        except OSError as e:
            logger.error("Failed to start RHVoice: %s", e)
            return False
        try:
            play_proc = subprocess.Popen(
                ["aplay", "-q"], stdin=rh_proc.stdout,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            # Without a reader RHVoice-test would wait on its pipe forever.
            rh_proc.kill()
            rh_proc.wait()
            self._error_message = _player_missing()
            self._error_detail = str(e)
            return False
        finally:
            # aplay owns the read end now; keeping our copy open leaks the fd
            # and would keep RHVoice-test from seeing a closed pipe.
            if rh_proc.stdout:
                rh_proc.stdout.close()
        threading.Thread(target=_feed_stdin, args=(rh_proc, text.encode("utf-8")), daemon=True).start()
        self._rh_proc = rh_proc
        self._begin_playback(play_proc, self._dispatch_gen, self._request_id)
        return True

    def _default_espeak_voice(self) -> str:
        """System-language espeak voice, never a silent English default."""
        lang = get_system_language()
        if lang == "pt":
            loc = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "").lower()
            return "pt" if loc.startswith("pt_pt") else "pt-br"
        return lang or "pt-br"

    def _speak_espeak(
        self,
        text: str,
        voice_id: str,
        rate: int,
        pitch: int,
        volume: int,
    ) -> bool:
        """Speak via espeak-ng (native FFI, or the espeak-ng command as fallback).

        Synthesis runs in a worker thread; long texts are streamed in chunks.
        volume == 0 is a true mute (no audible output).
        """
        voice = voice_id.removeprefix("espeak-") or self._default_espeak_voice()
        wpm, esp_pitch, esp_vol = espeak_wpm(rate), espeak_pitch(pitch), espeak_volume(volume)
        if esp_vol <= 0:
            return True
        engine = _get_tts_engine()

        def synth(chunk: str) -> str | None:
            path = _temp_wav()
            if engine and hasattr(engine, "synthesize_espeak"):
                try:
                    wav = engine.synthesize_espeak(chunk, voice, wpm, esp_pitch, esp_vol)
                    if wav and len(wav) > 100:
                        with open(path, "wb") as f:
                            f.write(wav)
                        return path
                except Exception as e:  # PyO3 errors: fall back to the command
                    logger.warning("Native espeak synthesis failed, using espeak-ng: %s", e)
            # Text on stdin, never in argv: no option injection ("-5 graus")
            # and no 128 KiB argument limit.
            cmd = ["espeak-ng", "-v", voice, "-s", str(wpm), "-p", str(esp_pitch),
                   "-a", str(esp_vol), "-w", path, "--stdin"]
            try:
                proc = subprocess.run(cmd, input=chunk.encode("utf-8"), capture_output=True, timeout=120)
                if proc.returncode == 0 and os.path.getsize(path) > 100:
                    return path
                self._stderr_tail.append(proc.stderr.decode(errors="replace").strip())
            except (OSError, subprocess.TimeoutExpired) as e:
                logger.error("espeak-ng failed: %s", e)
                self._stderr_tail.append(str(e))
            _unlink(path)
            return None

        self._run_synthesis(
            text, synth, lambda path: ["aplay", "-q", path],
            _("espeak-ng could not read this text. Check that espeak-ng is installed."),
        )
        return True

    def _run_synthesis(
        self,
        text: str,
        synth: Callable[[str], str | None],
        play_cmd: Callable[[str], list[str]],
        fail_message: str,
    ) -> None:
        """Synthesize and play ``text`` in a worker thread.

        ``synth(chunk)`` returns the path of a WAV file (owned by the caller
        from then on) or None. A short text is one file, kept for the history
        audio; a long one is streamed: chunk N+1 is synthesized while chunk N
        plays, so sound starts after the first chunk.
        """
        chunks = chunk_text(text, max_chars=_STREAM_CHUNK_CHARS) if len(text) > _STREAM_CHUNK_CHARS else [text]
        gen, req = self._dispatch_gen, self._request_id
        if len(chunks) > 1:
            target, args = self._stream, (chunks, synth, play_cmd, gen, req, fail_message)
        else:
            target, args = self._synth_once, (text, synth, play_cmd, gen, req, fail_message)
        thread = threading.Thread(target=target, args=args, daemon=True)
        self._bg_thread = thread
        thread.start()

    def _synth_once(self, text, synth, play_cmd, gen: int, req: int, fail_message: str) -> None:
        path = synth(text)
        if not self._is_current(gen):
            if path:
                _unlink(path)
            return
        if path is None:
            self._fail(fail_message, "\n".join(self._stderr_tail))
            return
        try:
            proc = subprocess.Popen(play_cmd(path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            _unlink(path)
            self._fail(_player_missing(), str(e))
            return
        # The file lives until playback ends (the history may keep a copy).
        self._begin_playback(proc, gen, req, path)

    def _stream(self, chunks: list[str], synth, play_cmd, gen: int, req: int, fail_message: str) -> None:
        """Stream ``chunks``: synthesize the next one while the current plays.

        Each file is deleted once its player has finished, so /tmp (often RAM)
        holds at most two chunks however long the text is.
        """
        played = 0
        playing: str | None = None  # file of the chunk being played
        play_proc: subprocess.Popen | None = None
        cur: str | None = None
        nxt = synth(chunks[0])
        try:
            for i in range(len(chunks)):
                cur, nxt = nxt, None
                if not self._is_current(gen):
                    break
                if cur is None:  # this chunk failed: try the next one
                    nxt = synth(chunks[i + 1]) if i + 1 < len(chunks) else None
                    continue
                if play_proc is not None:
                    finished = self._wait_player(play_proc, gen)
                    _unlink(playing)
                    playing = None
                    if not finished:
                        break
                try:
                    play_proc = subprocess.Popen(
                        play_cmd(cur), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except OSError as e:
                    self._fail(_player_missing(), str(e))
                    return
                playing, cur = cur, None
                played += 1
                if not self._begin_playback(play_proc, gen, req):
                    break
                if i + 1 < len(chunks):
                    nxt = synth(chunks[i + 1])
            if play_proc is not None:
                self._wait_player(play_proc, gen)
        finally:
            for path in (playing, cur, nxt):
                _unlink(path)
        if self._is_current(gen) and not played:
            self._fail(fail_message, "\n".join(self._stderr_tail))

    def _wait_player(self, proc: subprocess.Popen, gen: int) -> bool:
        """Wait for a chunk to finish playing; False if stopped meanwhile."""
        while proc.poll() is None:
            if not self._is_current(gen):
                _terminate(proc)
                return False
            time.sleep(0.03)
        return self._is_current(gen)

    def _speak_piper(
        self, text: str, voice_id: str, rate: int, pitch: int, volume: int
    ) -> bool:
        """Speak via Piper (native ONNX engine, or the piper command as fallback).

        voice_id format: "piper:/absolute/path/to/model.onnx". The native engine
        applies the volume itself; the command path plays through sox for it.
        """
        model_path = voice_id.removeprefix("piper:")
        if not os.path.isfile(model_path):
            logger.error("Piper model not found: %s", model_path)
            self._error_message = _("The selected Piper voice is not installed. Choose another voice in the Voice Manager.")
            self._error_action = "voice-manager"
            return False
        vol_factor = volume_factor(volume)
        if vol_factor <= 0.0:
            return True  # true mute — nothing audible
        length_scale, noise_scale, noise_w = piper_length_scale(rate), piper_noise_scale(pitch), 0.8
        engine = _get_tts_engine()
        piper_bin: str | None = None

        def synth(chunk: str) -> str | None:
            nonlocal piper_bin
            path = _temp_wav()
            if engine:
                try:
                    wav = engine.synthesize_piper(chunk, model_path, length_scale, noise_scale, noise_w, vol_factor)
                    if wav and len(wav) >= 100:
                        with open(path, "wb") as f:
                            f.write(wav)
                        return path
                except Exception as e:  # PyO3 errors: fall back to the command
                    logger.warning("Native Piper synthesis failed: %s", e)
            from services.voice_manager import _find_piper_binary

            piper_bin = piper_bin or _find_piper_binary()
            if piper_bin:
                try:
                    proc = subprocess.Popen(
                        [piper_bin, "--model", model_path, "--output_file", path,
                         "--length_scale", f"{length_scale:.2f}",
                         "--noise_scale", f"{noise_scale:.3f}",
                         "--noise_w", f"{noise_w:.2f}", "--sentence_silence", "0.05"],
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    )
                    self._piper_proc = proc
                    _out, err = proc.communicate(chunk.encode("utf-8"))
                    if proc.returncode == 0 and os.path.getsize(path) >= 100:
                        return path
                    if proc.returncode > 0:
                        self._stderr_tail.append(err.decode(errors="replace").strip()[-300:])
                except OSError as e:
                    logger.error("piper failed: %s", e)
            _unlink(path)
            return None

        def play_cmd(path: str) -> list[str]:
            if not engine and vol_factor != 1.0 and shutil.which("play"):
                return ["play", "-q", path, "vol", f"{vol_factor:.2f}"]
            return ["aplay", "-q", path]

        self._run_synthesis(text, synth, play_cmd, _("Piper could not read this text. Try another voice."))
        return True

    def _speak_kokoro_koko(
        self, text: str, voice_id: str, rate: int, pitch: int, volume: int
    ) -> bool:
        """Speak via the koko binary (biglinux-kokoro-tts package).

        ``koko pipe`` reads the text from stdin one line at a time and streams
        each one to the speaker as soon as it is synthesized. The command is
        built by kokoro_voice_service, the same code the Voice Manager preview
        uses.
        """
        if volume <= 0:
            return True  # true mute — nothing audible

        from services.kokoro_voice_service import (
            KOKO_AUDIO_STARTED_MARKER,
            build_koko_command,
            koko_problem,
            koko_workdir,
            kokoro_speed,
            prepare_koko_text,
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
        text = prepare_koko_text(text)
        if not text:
            return True  # only punctuation: nothing to say
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
                # Fed from a thread: `koko pipe` reads one line, synthesizes
                # it, then reads the next, so a text larger than the pipe
                # buffer (64 KB) would block this (GTK) thread for minutes.
                threading.Thread(
                    target=_feed_stdin, args=(proc, text.encode("utf-8")), daemon=True
                ).start()

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
                elif not line.startswith(_KOKO_TEXT_LINES):
                    self._stderr_tail.append(line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _begin_playback(
        self, proc: subprocess.Popen, gen: int, req: int, path: str | None = None,
    ) -> bool:
        """Track ``proc`` as the player of request ``req`` (any thread).

        ``path`` is the file it plays, deleted when playback ends. Returns
        False (and stops the player) if the request was stopped meanwhile.
        """
        if not self._is_current(gen):
            _terminate(proc)
            _unlink(path)
            return False
        if req != self._request_id:
            # An earlier reading in simultaneous mode: keep it reachable by
            # Stop/Pause without taking over the newest reading.
            self._overlapped.append((proc, path))
            return True
        self._process = proc
        if path is not None:
            self._piper_tmp_path = path
        # stop() may have run between the check above and the assignment: it
        # bumps the generation before collecting processes, so either it saw
        # this player or this check sees the new generation.
        if not self._is_current(gen):
            _terminate(proc)
            return False
        self._mark_audio_started()
        return True

    def _overlap_current(self) -> None:
        """Simultaneous mode: the running reading goes on beside the new one."""
        players = [(self._process, self._piper_tmp_path), (self._rh_proc, None)]
        self._process = self._rh_proc = self._piper_tmp_path = None
        for proc, path in players:
            if proc is not None and proc.poll() is None:
                self._overlapped.append((proc, path))
            else:
                _unlink(path)
        if self._audio_started and self._last_text:
            self._record_history(None)  # it has been heard; record it now

    def _mark_audio_started(self) -> None:
        self._audio_started = True
        if self._state == TTSState.LOADING:
            self._set_state(TTSState.SPEAKING)

    def _fail(self, message: str, detail: str = "", action: str = "retry") -> None:
        """Report a failure the person can act on (any thread)."""
        self._error_action = action
        self._error_message = message
        self._error_detail = (detail or "").strip()[-600:]
        logger.warning("TTS error: %s", message)
        logger.debug("TTS error detail: %s", self._error_detail)
        self._set_state(TTSState.ERROR)

    def _has_active_bg_thread(self) -> bool:
        """Whether the synthesis worker of the current request is still alive."""
        return self._bg_thread is not None and self._bg_thread.is_alive()

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
        if self._overlapped:
            still = []
            for proc, path in self._overlapped:
                if proc.poll() is None:
                    still.append((proc, path))
                else:
                    _unlink(path)
            self._overlapped = still
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
            # A positive status is the engine reporting a failure. A negative
            # one is a signal: SIGTERM/SIGKILL come from stop/supersede (not an
            # error), but SIGABRT & co. mean the engine itself crashed.
            crashed = rc < 0 and -rc in {int(sig) for sig in _CRASH_SIGNALS}
            if crashed and self._state != TTSState.ERROR:
                self._watch_id = 0
                self._fail(
                    _("{engine} crashed while reading the text.").format(
                        engine=engine_name(self._active_backend)
                    ),
                    "\n".join(self._stderr_tail),
                )
                return False
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

            tmp_path, self._piper_tmp_path = self._piper_tmp_path, None
            if self._audio_started:
                self._record_history(tmp_path)  # takes over the file
            else:
                _unlink(tmp_path)
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

    def _record_history(self, audio_path: str | None) -> None:
        """Record the reading that was just heard, if history is enabled.

        Takes over ``audio_path`` (deleted afterwards). The copy and the
        database write run in a worker: a long reading's WAV is large.
        """
        history = getattr(self._settings, "history", None) if self._settings else None
        if not history or not history.enabled or not self._last_text:
            _unlink(audio_path)
            return
        entry = {
            "text": self._last_text,
            "backend": self._last_backend,
            "voice_id": self._last_voice_id,
            "save_audio": history.save_audio,
            "save_text": history.save_text,
            "max_entries": history.max_entries,
            "max_age_days": history.max_age_days,
        }

        def _save() -> None:
            try:
                from services.history_service import save_history_entry

                save_history_entry(audio_path=audio_path, **entry)
            except Exception as e:
                logger.error("Failed to save history: %s", e)
            finally:
                _unlink(audio_path)

        threading.Thread(target=_save, daemon=True).start()

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
        if self.is_speaking or self._overlapped:
            self.stop()
