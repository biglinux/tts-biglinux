"""Speech pipeline fixes: pause/overlap, Stop reaching every process, Piper
discovery and volume, Kokoro's unsupported sliders.

Real processes stand in for the engines and players (no sound); the fake
programs live in tests/tmp/ (/tmp may be noexec).
"""
import importlib
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from fake_engines import install, make_bin_dir, text_wav, use_fakes

gi = pytest.importorskip("gi")
from gi.repository import GLib  # noqa: E402  (first import on the main thread, not in a worker)

ts = importlib.import_module("services.tts_service")
vm = importlib.import_module("services.voice_manager")
config = importlib.import_module("config")
TTSState = config.TTSState


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    use_fakes(monkeypatch, tmp_path)
    return tmp_path


def _pump():
    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass


def _until(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _pump()
        if condition():
            return True
        time.sleep(0.02)
    return False


def _run_watch(svc, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _pump()
        if svc._check_process() is False:
            _pump()
            return
        time.sleep(0.02)
    raise AssertionError("request did not finish")


def _proc_state(proc: subprocess.Popen) -> str:
    """Kernel state letter of a live process ("T" = stopped)."""
    try:
        stat = Path(f"/proc/{proc.pid}/stat").read_text()
    except OSError:
        return "gone"
    return stat.rsplit(")", 1)[1].split()[0]


def _reading(svc, text="x", backend="espeak-ng"):
    reading = ts._Reading(id=text, text=text, processed=text, backend=backend, voice_id="", gen=svc._generation)
    svc._reading = reading
    return reading


# ── 1. simultaneous speak while paused ───────────────────────────────


def test_simultaneous_speak_while_paused_continues_the_paused_reading(fakes, monkeypatch):
    monkeypatch.setenv("APLAY_SECONDS", "5")
    svc = ts.TTSService()
    assert svc.speak("Primeira.", backend="rhvoice", voice_id="x", volume=50)
    assert _until(lambda: svc.state is TTSState.SPEAKING)
    first = svc._reading
    assert svc.pause()
    player = first.procs[-1]
    assert _until(lambda: _proc_state(player) == "T", 2)

    assert svc.speak("Segunda.", backend="rhvoice", voice_id="x", volume=50, stop_previous=False)
    assert svc._overlapped == [first]
    assert not svc.is_paused
    assert _proc_state(player) not in ("T", "gone")  # continued, not frozen for ever

    procs = svc._audio_procs()
    started = time.monotonic()
    svc.stop()
    assert time.monotonic() - started < 1.0  # no 0.5 s wait per frozen process
    assert all(p.returncode is not None for p in procs)  # all reaped


# ── 5. pause reaches players started later ───────────────────────────


def test_pause_while_nothing_is_live_freezes_the_next_player(fakes, monkeypatch):
    monkeypatch.setenv("APLAY_SECONDS", "0.3")
    svc = ts.TTSService()
    reading = _reading(svc, "Texto")
    svc._set_state(TTSState.LOADING)
    gate = threading.Event()

    def synth(chunk):  # the voice is still "loading" until the gate opens
        gate.wait(5)
        return text_wav(Path(ts._temp_wav()), chunk)

    svc._run_synthesis(reading, synth, lambda path: ["aplay", "-q", path], "fail")
    assert svc._audio_procs() == []
    assert svc.pause()  # nothing live yet: the reading is paused anyway
    assert svc.is_paused
    gate.set()
    assert _until(lambda: reading.procs and _proc_state(reading.procs[-1]) == "T", 3)
    player = reading.procs[-1]
    time.sleep(0.5)  # longer than the chunk: it must not play while paused
    assert player.poll() is None
    assert reading.heard(time.monotonic(), svc._paused_since) == 0.0  # paused before any sound

    assert svc.resume()
    time.sleep(0.1)
    assert reading.heard(time.monotonic(), None) < 0.3  # the pause is not counted as heard
    assert player.wait(timeout=3) == 0
    reading.threads[0].join(timeout=3)
    assert not reading.threads[0].is_alive()


def test_resume_before_the_player_starts_lets_it_play(fakes, monkeypatch):
    monkeypatch.setenv("APLAY_SECONDS", "0.05")
    svc = ts.TTSService()
    reading = _reading(svc, "Texto")
    svc._set_state(TTSState.LOADING)
    gate = threading.Event()

    def synth(chunk):
        gate.wait(5)
        return text_wav(Path(ts._temp_wav()), chunk)

    svc._run_synthesis(reading, synth, lambda path: ["aplay", "-q", path], "fail")
    assert svc.pause()
    assert svc.resume()
    gate.set()
    reading.threads[0].join(timeout=5)
    assert reading.procs and reading.procs[-1].returncode == 0


def test_pause_state_is_cleared_when_the_request_ends(fakes):
    svc = ts.TTSService()
    _reading(svc, "x")
    svc._set_state(TTSState.SPEAKING)
    assert svc.pause()
    svc._reading = None
    assert svc._check_process() is False  # nothing left: back to IDLE
    assert not svc._paused
    _reading(svc, "y")
    svc._set_state(TTSState.SPEAKING)
    assert svc.pause()  # a stale pause never blocks the next one


def test_a_pause_that_ended_before_any_sound_is_forgotten(fakes):
    svc = ts.TTSService()
    reading = _reading(svc, "Texto")
    reading.paused_for = 0.8  # paused and resumed while the voice loaded
    svc._on_audio(reading)
    later = reading.audio_started_at + 0.5
    assert abs(reading.heard(later, None) - 0.5) < 1e-6  # not 0.0


# ── 6. Stop reaches the espeak-ng command ────────────────────────────

ESPEAK_HANG = f"""#!{sys.executable}
import sys, time
sys.stdin.buffer.read()
time.sleep(30)
"""


def test_stop_kills_the_espeak_ng_command(fakes, monkeypatch):
    install(make_bin_dir_on_path(monkeypatch), "espeak-ng", ESPEAK_HANG)
    monkeypatch.setattr(ts, "_get_tts_engine", lambda: None)
    svc = ts.TTSService()
    assert svc.speak("Texto qualquer.", backend="espeak-ng", voice_id="espeak-pt-br", volume=50)
    reading = svc._reading
    assert _until(lambda: reading.live_procs(), 3)
    espeak = reading.live_procs()[0]
    started = time.monotonic()
    svc.stop()
    assert espeak.wait(timeout=2) is not None
    reading.threads[0].join(timeout=3)
    assert not reading.threads[0].is_alive()
    assert time.monotonic() - started < 3


def test_a_hung_espeak_ng_is_stopped_but_a_pause_is_not_counted(fakes, monkeypatch):
    install(make_bin_dir_on_path(monkeypatch), "espeak-ng", ESPEAK_HANG)
    monkeypatch.setattr(ts, "_get_tts_engine", lambda: None)
    monkeypatch.setattr(ts, "_ESPEAK_SYNTH_TIMEOUT", 2.0)
    svc = ts.TTSService()
    assert svc.speak("Texto qualquer.", backend="espeak-ng", voice_id="espeak-pt-br", volume=50)
    reading = svc._reading
    assert _until(lambda: reading.live_procs(), 3)
    espeak = reading.live_procs()[0]

    svc._paused = True  # time paused does not count
    time.sleep(3.2)
    assert espeak.poll() is None
    svc._paused = False

    assert espeak.wait(timeout=5) is not None  # then the 2 s budget runs out
    reading.threads[0].join(timeout=3)
    assert not reading.threads[0].is_alive()
    assert any("too long" in line for line in svc._stderr_tail)
    svc.stop()


def make_bin_dir_on_path(monkeypatch) -> Path:
    import os

    bindir = make_bin_dir()
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return bindir


# ── 7. a process that ignores SIGTERM is reaped ──────────────────────


def test_stop_reaps_a_process_that_ignores_sigterm(fakes):
    svc = ts.TTSService()
    reading = _reading(svc, "x")
    stubborn = subprocess.Popen(
        [sys.executable, "-c",
         "import signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
         "print('ready', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE,
    )
    assert stubborn.stdout.readline().strip() == b"ready"
    reading.procs.append(stubborn)
    svc.stop()
    assert stubborn.returncode is not None  # killed and waited: no zombie
    stubborn.stdout.close()


# ── 3. Piper CLI fallback applies the volume ─────────────────────────

PIPER = f"""#!{sys.executable}
import sys, wave
args = sys.argv[1:]
out = args[args.index("--output_file") + 1]
sys.stdin.buffer.read()
w = wave.open(out, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
w.writeframes(b"\\x01\\x00" * 2205); w.close()
"""

PLAY = f"""#!{sys.executable}
import os, sys
open(os.environ["PLAYARGS"], "a").write(" ".join(sys.argv[1:]) + "\\n")
"""


def test_piper_cli_fallback_applies_the_volume_when_native_fails(fakes, monkeypatch):
    bindir = make_bin_dir_on_path(monkeypatch)
    piper = install(bindir, "piper-tts", PIPER)
    install(bindir, "play", PLAY)
    log = fakes / "play.args"
    monkeypatch.setenv("PLAYARGS", str(log))
    model = fakes / "voice.onnx"
    model.write_bytes(b"x")

    class Broken:  # the native module loaded, but fails for this model
        def synthesize_piper(self, *args):
            raise RuntimeError("unsupported model")

    monkeypatch.setattr(ts, "_get_tts_engine", lambda: Broken())
    monkeypatch.setattr(vm, "_find_piper_binary", lambda: str(piper))
    svc = ts.TTSService()
    assert svc.speak("Olá.", backend="piper", voice_id=f"piper:{model}", volume=25)
    _run_watch(svc)
    assert svc.state is TTSState.IDLE
    assert log.exists() and "vol 0.50" in log.read_text()


# ── 2. Piper voices are listed with the native engine only ───────────


def _piper_voice(home: Path) -> Path:
    d = home / ".local" / "share" / "piper-voices" / "pt" / "pt_BR" / "faber" / "medium"
    d.mkdir(parents=True)
    onnx = d / "pt_BR-faber-medium.onnx"
    onnx.write_bytes(b"x")
    Path(str(onnx) + ".json").write_text("{}")
    return onnx


def test_piper_voices_found_with_the_native_engine_and_no_binary(tmp_path, monkeypatch):
    onnx = _piper_voice(tmp_path)
    monkeypatch.setattr(vm.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(vm, "_find_piper_binary", lambda: None)
    monkeypatch.setitem(sys.modules, "tts_engine", types.ModuleType("tts_engine"))
    ids = [v.voice_id for v in vm._discover_piper_voices()]
    assert f"piper:{onnx}" in ids


def test_no_piper_voices_without_any_piper_engine(tmp_path, monkeypatch):
    _piper_voice(tmp_path)
    monkeypatch.setattr(vm.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(vm, "_find_piper_binary", lambda: None)
    monkeypatch.setitem(sys.modules, "tts_engine", None)  # import fails
    assert vm._discover_piper_voices() == []


# ── 3. Kokoro: pitch slider insensitive, volume only mutes ───────────


@pytest.mark.parametrize("backend", ["kokoro", "piper", "rhvoice"])
def test_kokoro_disables_pitch_and_explains_volume(backend):
    mv = importlib.import_module("ui.main_view")
    view = SimpleNamespace(
        _pitch_scale=mock.Mock(), _pitch_row=mock.Mock(),
        _volume_scale=mock.Mock(), _volume_row=mock.Mock(),
    )
    mv.MainView._update_kokoro_unsupported_sliders(view, backend)
    view._pitch_scale.set_sensitive.assert_called_with(backend != "kokoro")
    # Volume stays usable everywhere: Kokoro honors mute (0).
    view._volume_scale.set_sensitive.assert_not_called()
    for row in (view._pitch_row, view._volume_row):
        tip = row.set_tooltip_text.call_args.args[0]
        assert (tip is None) == (backend != "kokoro")
