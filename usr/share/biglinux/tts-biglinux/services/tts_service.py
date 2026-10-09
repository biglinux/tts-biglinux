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
from dataclasses import dataclass, field
from typing import Any

from config import TTSBackend, TTSState
from services.history_service import Recording, new_entry_id, new_recording, save_reading, wav_layout
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

# Engine stderr lines that are progress, not errors — and koko's lines that
# contain the text being read, which must never reach error details or logs.
_ENGINE_NOISE = (
    "CALLING PHONEMIZE ON:", "phonemes:", "voice styles loaded", "Entering streaming mode",
    "Audio written to stdout", "shape_style", "MANUAL LANGUAGE MODE", "Processing segment",
    "WARNING: Character", "TOKENIZE:", "Language detection confidence", "Detected language",
    "Using manually specified language", "Using standard voices file", "Manual language mode",
    "Processing chunk with language", "All text processed",
)

# Backends that synthesize before any sound is heard: their requests start in
# LOADING and switch to SPEAKING when playback really begins.
_SYNTHESIZE_FIRST = frozenset({
    TTSBackend.RHVOICE.value,
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


@dataclass(eq=False)
class _Reading:
    """One speak() request: what is read, by which engine, and its outcome.

    Every process, temporary file, worker thread and the history recording of
    a reading hang off this object, so simultaneous and queued readings never
    mix, and one place (TTSService._finish_reading) decides what reaches the
    history.
    """

    id: str
    text: str  # as given (after the character limit)
    processed: str  # what the engine was given
    backend: str
    voice_id: str
    gen: int
    started: float = field(default_factory=time.time)
    recording: Recording | None = None
    procs: list[subprocess.Popen] = field(default_factory=list)
    engines: list[subprocess.Popen] = field(default_factory=list)  # synthesizers (exit status matters)
    files: list[str] = field(default_factory=list)  # temporary files, removed at the end
    threads: list[threading.Thread] = field(default_factory=list)
    audio_started: bool = False
    audio_started_at: float = 0.0  # monotonic; with paused_for: how much was heard
    paused_for: float = 0.0
    finished: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def alive(self) -> bool:
        if any(t.is_alive() for t in self.threads):
            return True
        return any(p.poll() is None for p in self.procs)

    def live_procs(self) -> list[subprocess.Popen]:
        return [p for p in self.procs if p.poll() is None]

    def heard(self, now: float, paused_since: float | None) -> float | None:
        """Seconds of audio played so far (None if no sound yet)."""
        if not self.audio_started:
            return None
        paused = self.paused_for + (now - paused_since if paused_since else 0.0)
        return max(0.0, now - self.audio_started_at - paused)


class TTSService:
    """Speak/stop lifecycle for every backend."""

    def __init__(self, settings=None) -> None:
        self._state: TTSState = TTSState.IDLE
        self._on_state_changed: OnStateChanged | None = None
        self._on_state_changed_extra: list[OnStateChanged] = []
        self._watch_id: int = 0
        self._settings = settings  # AppSettings reference
        # The reading in progress, and earlier ones still playing beside it
        # ("simultaneous" mode): Stop and Pause reach all of them.
        self._reading: _Reading | None = None
        self._overlapped: list[_Reading] = []
        # Player of the current reading (the watch and the tests follow it).
        self._process: subprocess.Popen | None = None
        self._last_text: str = ""  # for "read again" (media keys)
        self._last_spoken_text: str = ""  # shown in notifications
        # Monotonic request generation, bumped by stop(). Workers capture it at
        # dispatch and never start audio for a generation that is gone: stale
        # audio from a previous Alt+V cannot start after a newer one.
        # Simultaneous readings share a generation (only stop() ends them).
        self._generation: int = 0
        # Pause/resume: SIGSTOP/SIGCONT the processes of the readings. A
        # stopped process keeps poll()==None, so the state stays SPEAKING and
        # playback freezes and resumes in place.
        self._paused: bool = False
        self._paused_since: float | None = None  # monotonic
        # Why the last request failed: a translated sentence that says how to
        # fix it, plus optional technical detail (engine stderr). Kept until
        # the next speak()/stop() so the UI can show it.
        self._error_message: str = ""
        self._error_detail: str = ""
        # What the UI can offer to fix it: "voice-manager", "retry" or "".
        self._error_action: str = ""
        self._active_backend: str = ""
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

    def _readings(self) -> list[_Reading]:
        return ([self._reading] if self._reading else []) + list(self._overlapped)

    def _audio_procs(self) -> list[subprocess.Popen]:
        """Live processes of every reading, paused and resumed as a group."""
        return [p for r in self._readings() for p in r.live_procs()]

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
        self._paused_since = time.monotonic()
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
        if self._paused_since is not None:
            elapsed = time.monotonic() - self._paused_since
            for reading in self._readings():
                reading.paused_for += elapsed
        self._paused = False
        self._paused_since = None
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
            # No sleep here: speak() may run on the GTK main thread.
            self.stop()
        else:
            self._overlap_current()

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

        reading = _Reading(
            id=new_entry_id(), text=text, processed=processed,
            backend=backend, voice_id=voice_id, gen=self._generation,
        )
        reading.recording = self._new_recording(reading)
        self._reading = reading
        self._process = None
        self._active_backend = backend
        self._audio_started = False
        self._stderr_tail.clear()

        if backend == TTSBackend.RHVOICE.value:
            success = self._speak_rhvoice(reading, voice_id, rate, pitch, volume)
        elif backend == TTSBackend.ESPEAK_NG.value:
            success = self._speak_espeak(reading, voice_id, rate, pitch, volume)
        elif backend == TTSBackend.PIPER.value:
            success = self._speak_piper(reading, voice_id, rate, pitch, volume)
        elif backend == TTSBackend.KOKORO.value:
            success = self._speak_kokoro(reading, voice_id, rate, pitch, volume)
        else:
            logger.error("Unknown backend: %s", backend)
            success = False

        if success:
            self._paused = False
            self._stopped_by_user = False
            self._last_spoken_text = processed
            self._last_text = text
            if self._audio_started or backend not in _SYNTHESIZE_FIRST:
                self._set_state(TTSState.SPEAKING)
            else:
                self._set_state(TTSState.LOADING)
            self._start_watch()
        else:
            self._reading = None
            self._finish_reading(reading, "error")
            if not self._error_message:
                self._error_message = _("{engine} could not start. Check that it is installed.").format(
                    engine=engine_name(backend)
                )
            if not self._error_action:
                self._error_action = "retry"
            self._set_state(TTSState.ERROR)

        return success

    def stop(self) -> None:
        """Stop every reading immediately (recorded as stopped if heard)."""
        was_busy = self._state in (TTSState.LOADING, TTSState.SPEAKING)
        # Invalidate any in-flight background synthesis so it won't start audio.
        self._generation += 1
        self._stop_watch()
        self._error_message = ""
        self._error_detail = ""

        readings = self._readings()
        now, paused_since = time.monotonic(), self._paused_since if self._paused else None
        heard = {reading.id: reading.heard(now, paused_since) for reading in readings}
        self._reading = None
        self._overlapped = []
        self._process = None
        procs = [p for r in readings for p in r.live_procs()]
        # A SIGSTOP'd process ignores SIGTERM until continued.
        if self._paused:
            for p in procs:
                try:
                    p.send_signal(signal.SIGCONT)
                except (ProcessLookupError, OSError):
                    pass
            self._paused = False
        self._paused_since = None
        # stop() may run on the GTK main thread: the reap is bounded and short
        # (aplay, RHVoice-test, piper and koko die at once). Workers see the new
        # generation and exit at their next check; they are not joined here.
        for proc in procs:
            try:
                proc.terminate()
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
        for reading in readings:
            self._finish_reading(reading, "stopped", heard[reading.id])

        self._stopped_by_user = was_busy
        self._set_state(TTSState.IDLE)
        logger.debug("Speech stopped")

    def toggle(self, text: str, **kwargs: Any) -> bool:
        """Toggle speak/stop — the shortcut behavior. True if state changed."""
        if self.is_speaking:
            self.stop()
            return True
        if text and text.strip():
            return self.speak(text, **kwargs)
        return False

    # ── History ──────────────────────────────────────────────────────

    def _history_config(self):
        return getattr(self._settings, "history", None) if self._settings else None

    def _new_recording(self, reading: _Reading) -> Recording | None:
        """Where the reading's audio is written as it plays (history on)."""
        history = self._history_config()
        if not history or not history.enabled or not history.save_audio:
            return None
        # koko writes its audio file itself (-o): straight into the history.
        external = reading.backend == TTSBackend.KOKORO.value
        return new_recording(reading.backend, reading.started, external=external)

    def _finish_reading(self, reading: _Reading, status: str, heard: float | None = None) -> None:
        """The reading ended (completed / stopped / error): keep it in the
        history if it was heard and history is on. Any thread; never blocks:
        the files are finished and indexed in a worker."""
        with reading.lock:
            if reading.finished:
                return
            reading.finished = True
        history = self._history_config()
        keep = bool(history and history.enabled and reading.audio_started)
        recording = reading.recording

        def _save() -> None:
            # The engine threads write the last audio before they end.
            for t in reading.threads:
                if t is not threading.current_thread():
                    t.join(timeout=3)
            try:
                if keep:
                    save_reading(
                        text=reading.text,
                        processed_text=reading.processed,
                        backend=reading.backend,
                        voice_id=reading.voice_id,
                        started=reading.started,
                        status=status,
                        recording=recording,
                        heard_seconds=heard,
                        save_audio=history.save_audio,
                        save_text=history.save_text,
                        max_entries=history.max_entries,
                        max_age_days=history.max_age_days,
                    )
                elif recording:
                    recording.discard()
            except Exception as e:  # history must never break a reading
                logger.error("Failed to save history: %s", e)
            finally:
                for path in reading.files:
                    _unlink(path)

        if keep or recording or reading.files:
            threading.Thread(target=_save, daemon=True).start()

    # ── Engines ──────────────────────────────────────────────────────

    def _speak_rhvoice(
        self, reading: _Reading, voice_id: str, rate: int, pitch: int, volume: int,
    ) -> bool:
        """Speak via RHVoice-test; its WAV goes through us to aplay."""
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
        return self._start_relay(
            reading, cmd, reading.processed,
            missing=_("RHVoice is not installed. Install the rhvoice package."),
        )

    def _speak_kokoro(
        self, reading: _Reading, voice_id: str, rate: int, pitch: int, volume: int,
    ) -> bool:
        """Speak via the koko binary (biglinux-kokoro-tts package).

        ``koko pipe`` reads one line at a time and plays each one as soon as it
        is synthesized; it says "Streaming audio" on stderr when sound starts.
        With ``-o`` it also writes all the audio it plays to that WAV file:
        the history recording itself, or a private scratch file.
        (``koko stream`` is not usable: it mixes log lines into the audio on
        stdout.) The command comes from kokoro_voice_service, shared with the
        Voice Manager preview.
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

        text = prepare_koko_text(reading.processed)
        if not text:
            return True  # only punctuation: nothing to say
        workdir = koko_workdir()
        if reading.recording is not None:
            output = str(reading.recording.path)
        else:
            output = os.path.join(workdir, f"pipe-{reading.id}.wav")  # unique: simultaneous readings
            reading.files.append(output)
        speed = kokoro_speed(rate, kokoro_cfg.emotion_preset if kokoro_cfg else "neutral")
        cmd = build_koko_command(
            voice_id, speed=speed, blend=blend, output=output,
            blend_ratio=kokoro_cfg.blend_ratio if kokoro_cfg else 0.5,
        )
        logger.info("Kokoro (koko binary): voice=%s, lang=%s, speed=%.2f", cmd[cmd.index("-s") + 1], cmd[cmd.index("-l") + 1], speed)
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=workdir,
            )
        except OSError as e:
            logger.error("Failed to start koko: %s", e)
            return False
        reading.procs.append(proc)
        reading.engines.append(proc)
        if reading is self._reading:
            self._process = proc
        # Fed from a thread: koko reads one line, synthesizes it, then reads
        # the next, so a text larger than the pipe buffer (64 KB) would block
        # this (GTK) thread for minutes.
        threading.Thread(target=_feed_stdin, args=(proc, (text + "\n").encode("utf-8")), daemon=True).start()
        reader = threading.Thread(
            target=self._read_engine_stderr, args=(proc, reading, KOKO_AUDIO_STARTED_MARKER), daemon=True,
        )
        reading.threads.append(reader)
        reader.start()
        return True

    def _start_relay(
        self, reading: _Reading, cmd: list[str], text: str, *, cwd: str | None = None,
        missing: str = "",
    ) -> bool:
        """Run an engine that writes a WAV stream to stdout, played by aplay.

        The bytes go through a relay thread: to the player, and the very same
        bytes to the history recording. Sound has started when the first
        samples reach the player.
        """
        try:
            source = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
            )
        except FileNotFoundError:
            logger.error("Command not found: %s", cmd[0])
            self._error_message = missing
            return False
        except OSError as e:
            logger.error("Failed to start %s: %s", cmd[0], e)
            return False
        try:
            player = subprocess.Popen(
                ["aplay", "-q"], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            # Without a reader the engine would wait on its pipe forever.
            source.kill()
            source.wait()
            self._error_message = _player_missing()
            self._error_detail = str(e)
            return False
        reading.procs += [source, player]
        reading.engines.append(source)
        if reading is self._reading:
            self._process = player
        # Fed from a thread: koko reads one line, synthesizes it, then reads
        # the next, so a text larger than the pipe buffer (64 KB) would block
        # this (GTK) thread for minutes.
        for target, args in (
            (_feed_stdin, (source, text.encode("utf-8"))),
            (self._read_engine_stderr, (source,)),
            (self._relay, (reading, source, player)),
        ):
            t = threading.Thread(target=target, args=args, daemon=True)
            if target == self._relay:
                reading.threads.append(t)
            t.start()
        return True

    def _relay(self, reading: _Reading, source: subprocess.Popen, player: subprocess.Popen) -> None:
        """Copy the engine's WAV stream to the player and to the recording."""
        fd = source.stdout.fileno()
        pending = b""
        header_done = False
        try:
            while True:
                data = os.read(fd, 65536)
                if not data:
                    break
                if not header_done:
                    # koko prints a line of text before the WAV header.
                    pending += data
                    start = pending.find(b"RIFF")
                    if start < 0:
                        pending = pending[-3:]
                        continue
                    pending = pending[start:]
                    layout = wav_layout(pending[:4096])
                    if layout is None and len(pending) < 4096:
                        continue
                    data, pending, header_done = pending, b"", True
                    has_samples = layout is not None and len(data) > layout[1]
                else:
                    has_samples = True
                try:
                    player.stdin.write(data)
                    player.stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    break  # the player was stopped
                if reading.recording:
                    reading.recording.write(data)
                if has_samples and not reading.audio_started:
                    self._on_audio(reading)
        except (OSError, ValueError):
            pass
        finally:
            for stream in (player.stdin, source.stdout):
                try:
                    stream.close()
                except (OSError, ValueError, BrokenPipeError):
                    pass

    def _read_engine_stderr(
        self, proc: subprocess.Popen, reading: _Reading | None = None, marker: str = "",
    ) -> None:
        """Keep the last lines an engine writes to stderr (error detail);
        ``marker`` in a line means the reading's sound has started."""
        stream = proc.stderr
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if marker and reading is not None and marker in line:
                    if not reading.audio_started:
                        self._on_audio(reading)
                elif line and not line.startswith(_ENGINE_NOISE):
                    self._stderr_tail.append(line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _default_espeak_voice(self) -> str:
        """System-language espeak voice, never a silent English default."""
        lang = get_system_language()
        if lang == "pt":
            loc = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "").lower()
            return "pt" if loc.startswith("pt_pt") else "pt-br"
        return lang or "pt-br"

    def _speak_espeak(
        self, reading: _Reading, voice_id: str, rate: int, pitch: int, volume: int,
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
            reading, synth, lambda path: ["aplay", "-q", path],
            _("espeak-ng could not read this text. Check that espeak-ng is installed."),
        )
        return True

    def _speak_piper(
        self, reading: _Reading, voice_id: str, rate: int, pitch: int, volume: int,
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
                    reading.procs.append(proc)
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

        self._run_synthesis(reading, synth, play_cmd, _("Piper could not read this text. Try another voice."))
        return True

    def _run_synthesis(
        self,
        reading: _Reading,
        synth: Callable[[str], str | None],
        play_cmd: Callable[[str], list[str]],
        fail_message: str,
    ) -> None:
        """Synthesize and play the reading in a worker thread.

        ``synth(chunk)`` returns the path of a WAV file (owned by the caller
        from then on) or None. A short text is one file; a long one is
        streamed: chunk N+1 is synthesized while chunk N plays, so sound starts
        after the first chunk. Each file played is added to the recording.
        """
        text = reading.processed
        chunks = chunk_text(text, max_chars=_STREAM_CHUNK_CHARS) if len(text) > _STREAM_CHUNK_CHARS else [text]
        thread = threading.Thread(
            target=self._stream, args=(reading, chunks, synth, play_cmd, fail_message), daemon=True,
        )
        reading.threads.append(thread)
        thread.start()

    def _stream(self, reading: _Reading, chunks: list[str], synth, play_cmd, fail_message: str) -> None:
        """Play ``chunks`` in order, synthesizing the next one while one plays.

        Each file is deleted once its player has finished, so /tmp (often RAM)
        holds at most two chunks however long the text is.
        """
        gen = reading.gen
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
                    if self._is_current(gen):
                        self._fail(_player_missing(), str(e))
                    return
                playing, cur = cur, None
                if not self._begin_playback(reading, play_proc):
                    break
                if reading.recording:
                    reading.recording.add_wav(playing)
                played += 1
                if i + 1 < len(chunks):
                    nxt = synth(chunks[i + 1])
            if play_proc is not None:
                self._wait_player(play_proc, gen)
        finally:
            for path in (playing, cur, nxt):
                _unlink(path)
        if self._is_current(gen) and not played and reading is self._reading:
            self._fail(fail_message, "\n".join(self._stderr_tail))

    def _wait_player(self, proc: subprocess.Popen, gen: int) -> bool:
        """Wait for a chunk to finish playing; False if stopped meanwhile."""
        while proc.poll() is None:
            if not self._is_current(gen):
                _terminate(proc)
                return False
            time.sleep(0.03)
        return self._is_current(gen)

    def _begin_playback(self, reading: _Reading, proc: subprocess.Popen) -> bool:
        """Track ``proc`` as a player of ``reading`` (any thread).

        Returns False (and stops the player) if the reading was stopped.
        """
        if not self._is_current(reading.gen) or reading.finished:
            _terminate(proc)
            return False
        if reading is not self._reading and reading not in self._overlapped:
            self._overlapped.append(reading)  # Stop and Pause must reach it
        reading.procs = reading.live_procs() + [proc]
        if reading is self._reading:
            self._process = proc
        # stop() may have run between the check above and the registration: it
        # bumps the generation before collecting processes, so either it saw
        # this player or this check sees the new generation.
        if not self._is_current(reading.gen):
            _terminate(proc)
            return False
        self._on_audio(reading)
        return True

    def _on_audio(self, reading: _Reading) -> None:
        """Sound of ``reading`` is being produced."""
        if not reading.audio_started:
            reading.audio_started_at = time.monotonic()
        reading.audio_started = True
        if reading is self._reading and self._is_current(reading.gen):
            self._mark_audio_started()

    def _overlap_current(self) -> None:
        """Simultaneous mode: the running reading goes on beside the new one."""
        reading, self._reading = self._reading, None
        self._process = None
        if reading is None:
            return
        if reading.alive():
            self._overlapped.append(reading)
        else:
            self._finish_reading(reading, "completed")

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
        reading = self._reading
        if reading is not None:
            for proc in reading.live_procs():
                _terminate(proc)
            self._finish_reading(reading, "error")
        self._set_state(TTSState.ERROR)

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

    def _engine_failure(self, reading: _Reading) -> str:
        """Why the reading's engine failed ("" if it did not)."""
        codes = [p.returncode for p in (*reading.engines, *reading.procs[-1:])]
        for rc in codes:
            if rc is None or rc == 0:
                continue
            logger.warning("TTS process exited with code %d", rc)
            if rc < 0 and -rc in {int(sig) for sig in _CRASH_SIGNALS}:
                return _("{engine} crashed while reading the text.").format(engine=engine_name(reading.backend))
            if rc > 0:
                return _("{engine} stopped with an error and could not read the text.").format(
                    engine=engine_name(reading.backend)
                )
        return ""

    def _check_process(self) -> bool:
        """Follow the readings; finish those that ended (GTK timer)."""
        for reading in list(self._overlapped):
            if not reading.alive():
                self._overlapped.remove(reading)
                self._finish_reading(reading, "completed")

        reading = self._reading
        if reading is not None:
            if reading.alive():
                return True
            failure = "" if reading.finished else self._engine_failure(reading)
            if failure and self._state != TTSState.ERROR:
                self._fail(failure, "\n".join(self._stderr_tail))
            else:
                self._finish_reading(
                    reading, "error" if self._state == TTSState.ERROR else "completed",
                )
            self._reading = None
            self._process = None
        if self._overlapped:
            return True  # earlier readings still playing: still speaking
        self._finish_request()
        return False

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
        """Stop every reading on shutdown (heard ones are kept as stopped)."""
        self._stop_watch()
        if self._readings():
            self.stop()
