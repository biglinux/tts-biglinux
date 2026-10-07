"""TTSService request lifecycle with a real subprocess standing in for koko.

A tiny shell script mimics koko's stderr protocol, so the process start, the
stderr reader thread, the LOADING → SPEAKING switch and the exit-status
handling all run for real — only the synthesizer itself is fake (no audio).
"""
import importlib
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

import pytest

ts = importlib.import_module("services.tts_service")
kvs = importlib.import_module("services.kokoro_voice_service")
config = importlib.import_module("config")
TTSState = config.TTSState

FAKE_OK = """#!/bin/sh
cat >/dev/null
echo "MANUAL LANGUAGE MODE: Using specified language: pt-br" >&2
sleep 0.3
echo "Streaming audio for this segment..." >&2
sleep 0.3
exit 0
"""

FAKE_FAIL = """#!/bin/sh
cat >/dev/null
echo "Error: Os { code: 13, kind: PermissionDenied, message: \\"Permission denied\\" }" >&2
exit 1
"""


def _fake_koko(tmp_path, monkeypatch, script):
    # /tmp may be mounted noexec: keep the fake executable inside the tree
    # (tests/tmp/ is git-ignored) and remove it afterwards.
    base = Path(__file__).resolve().parent / "tmp"
    base.mkdir(exist_ok=True)
    bindir = Path(tempfile.mkdtemp(dir=base))
    monkeypatch.setattr(kvs, "_test_cleanup", bindir, raising=False)
    import atexit

    atexit.register(shutil.rmtree, bindir, True)
    koko = bindir / "koko"
    koko.write_text(script)
    koko.chmod(koko.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"x")
    voices = tmp_path / "voices.bin"
    voices.write_bytes(b"x")
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("KOKO_MODEL_PATH", str(model))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(kvs, "_koko_workdir_cache", None)
    monkeypatch.setattr(kvs, "get_active_voices_bin", lambda: voices)
    monkeypatch.setattr(kvs, "get_installed_voice_ids", lambda: {"pm_alex", "pf_dora"})


def _speak(svc):
    return svc.speak("Olá, mundo.", backend="kokoro", voice_id="kokoro:pm_alex", volume=50)


def _pump():
    from gi.repository import GLib

    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass


def _run_watch(svc, timeout=5.0):
    """Drive the process watch and the main loop like GTK would."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _pump()
        if svc._check_process() is False:
            _pump()
            return
        time.sleep(0.05)
    raise AssertionError("request did not finish")


def test_kokoro_loads_then_speaks_then_returns_to_idle(tmp_path, monkeypatch):
    _fake_koko(tmp_path, monkeypatch, FAKE_OK)
    svc = ts.TTSService()
    seen = []
    svc.add_on_state_changed(seen.append)
    assert _speak(svc)
    assert svc.state is TTSState.LOADING  # no sound yet: the model is loading
    assert svc.is_speaking  # the shortcut can still cancel it
    end = time.monotonic() + 3
    while svc.state is TTSState.LOADING and time.monotonic() < end:
        time.sleep(0.02)
    assert svc.state is TTSState.SPEAKING  # koko said audio started
    _pump()
    _run_watch(svc)
    assert svc.state is TTSState.IDLE
    assert seen == [TTSState.LOADING, TTSState.SPEAKING, TTSState.IDLE]
    assert svc.last_error == ""


def test_engine_failure_is_reported_and_stays_visible(tmp_path, monkeypatch):
    _fake_koko(tmp_path, monkeypatch, FAKE_FAIL)
    svc = ts.TTSService()
    assert _speak(svc)
    _run_watch(svc)
    assert svc.state is TTSState.ERROR
    assert "Kokoro" in svc.last_error
    assert "Permission denied" in svc.last_error_detail
    # The watch must not silently turn the failure back into "ready".
    assert svc._check_process() is False
    assert svc.state is TTSState.ERROR
    # The next request clears it.
    svc.stop()
    assert svc.state is TTSState.IDLE and svc.last_error == ""


def test_missing_kokoro_voice_fails_with_a_fix(tmp_path, monkeypatch):
    _fake_koko(tmp_path, monkeypatch, FAKE_OK)
    monkeypatch.setattr(kvs, "get_installed_voice_ids", lambda: {"pf_dora"})
    svc = ts.TTSService()
    assert not _speak(svc)
    assert svc.state is TTSState.ERROR
    assert svc.last_error_action == "voice-manager"
    assert svc._process is None  # koko never started (it would pick another voice)


def test_stop_while_loading_cancels_without_error(tmp_path, monkeypatch):
    _fake_koko(tmp_path, monkeypatch, FAKE_OK)
    svc = ts.TTSService()
    assert _speak(svc)
    proc = svc._process
    svc.stop()
    assert svc.state is TTSState.IDLE and svc.stopped_by_user
    proc.wait(timeout=2)
    assert proc.returncode != 0  # killed, and that is not reported as a failure
    assert svc.last_error == ""


def test_stop_does_not_wake_speech_dispatcher(tmp_path, monkeypatch):
    calls = []
    real_run = ts.subprocess.run

    def spy(cmd, *a, **k):
        calls.append(cmd)
        return real_run(["true"])

    monkeypatch.setattr(ts.subprocess, "run", spy)
    svc = ts.TTSService()
    svc.stop()
    assert not any(c and c[0] == "spd-say" for c in calls)


def test_state_listeners_run_on_the_main_thread(monkeypatch):
    import threading

    from gi.repository import GLib

    svc = ts.TTSService()
    threads = []
    svc.add_on_state_changed(lambda s: threads.append(threading.current_thread()))
    worker = threading.Thread(target=lambda: svc._fail("boom"))
    worker.start()
    worker.join()
    assert threads == []  # not called from the worker
    ctx = GLib.MainContext.default()
    end = time.monotonic() + 2
    while not threads and time.monotonic() < end:
        ctx.iteration(False)
    assert threads == [threading.main_thread()]


@pytest.mark.parametrize(
    "blend,ratio,expected",
    [
        ("", 0.5, "pm_alex"),
        ("kokoro:pf_dora", 0.5, "pm_alex.5+pf_dora.5"),
        ("kokoro:pf_dora", 0.3, "pm_alex.7+pf_dora.3"),
        ("kokoro:pm_alex", 0.5, "pm_alex"),  # blending with itself is a no-op
        ("../etc", 0.5, "pm_alex"),  # never pass unsafe ids to koko
    ],
)
def test_koko_style_blend(blend, ratio, expected):
    assert kvs.koko_style("kokoro:pm_alex", blend, ratio) == expected


def test_preview_and_playback_share_the_koko_command(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(kvs, "_koko_workdir_cache", None)
    play = kvs.build_koko_command("kokoro:pm_alex", speed=1.0)
    preview = kvs.build_koko_command("kokoro:pm_alex", speed=1.0, text="Oi", output=str(tmp_path / "p.wav"))
    # Same model, voices, language and voice up to the subcommand.
    assert play[: play.index("pipe")] == preview[: preview.index("text")]
    assert play[play.index("-l") + 1] == "pt-br"
    assert preview[-2:] == ["--", "Oi"]  # text after "--": "-5 graus" is not an option
