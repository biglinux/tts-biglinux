"""TTSService request lifecycle with real subprocesses standing in for koko.

The fake programs (tests/fake_engines.py) speak koko pipe's protocol (audio
file and stderr), so process start, the stdin feeder, the stderr reader, the
LOADING → SPEAKING switch and the exit-status handling all run for real —
only the synthesizer is fake (no sound).
"""
import importlib
import time

import pytest

from fake_engines import use_fakes

ts = importlib.import_module("services.tts_service")
kvs = importlib.import_module("services.kokoro_voice_service")
config = importlib.import_module("config")
TTSState = config.TTSState


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    use_fakes(monkeypatch, tmp_path)
    return tmp_path


def _speak(svc, text="Olá, mundo."):
    return svc.speak(text, backend="kokoro", voice_id="kokoro:pm_alex", volume=50)


def _pump():
    from gi.repository import GLib

    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass


def _run_watch(svc, timeout=10.0):
    """Drive the process watch and the main loop like GTK would."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _pump()
        if svc._check_process() is False:
            _pump()
            return
        time.sleep(0.02)
    raise AssertionError("request did not finish")


def test_kokoro_loads_then_speaks_then_returns_to_idle(fakes, monkeypatch):
    monkeypatch.setenv("KOKO_MODE", "slow")  # the model "loads" for 1 s
    monkeypatch.setenv("APLAY_SECONDS", "0.5")
    svc = ts.TTSService()
    seen = []
    svc.add_on_state_changed(seen.append)
    assert _speak(svc)
    assert svc.state is TTSState.LOADING  # no sound yet: the model is loading
    assert svc.is_speaking  # the shortcut can still cancel it
    end = time.monotonic() + 5
    while svc.state is TTSState.LOADING and time.monotonic() < end:
        time.sleep(0.02)
    assert svc.state is TTSState.SPEAKING  # the first samples reached the player
    _pump()
    _run_watch(svc)
    assert svc.state is TTSState.IDLE
    assert seen == [TTSState.LOADING, TTSState.SPEAKING, TTSState.IDLE]
    assert svc.last_error == ""


def test_engine_failure_is_reported_and_stays_visible(fakes, monkeypatch):
    monkeypatch.setenv("KOKO_MODE", "fail")
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


def test_missing_kokoro_voice_fails_with_a_fix(fakes, monkeypatch):
    monkeypatch.setattr(kvs, "get_installed_voice_ids", lambda: {"pf_dora"})
    svc = ts.TTSService()
    assert not _speak(svc)
    assert svc.state is TTSState.ERROR
    assert svc.last_error_action == "voice-manager"
    assert svc._process is None  # koko never started (it would pick another voice)


def test_stop_while_loading_cancels_without_error(fakes, monkeypatch):
    monkeypatch.setenv("KOKO_MODE", "slow")
    svc = ts.TTSService()
    assert _speak(svc)
    procs = list(svc._reading.procs)
    svc.stop()
    assert svc.state is TTSState.IDLE and svc.stopped_by_user
    for proc in procs:
        proc.wait(timeout=2)
    assert any(p.returncode != 0 for p in procs)  # killed: not a failure
    assert svc.last_error == ""


def test_stop_does_not_wake_speech_dispatcher(monkeypatch):
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
    # koko pipe, never koko stream: stream mixes log lines into its audio.
    assert play[-3:] == ["pipe", "-o", str(tmp_path / "biglinux-tts" / "koko" / "pipe_output.wav")]
    assert play[:-3] == preview[: preview.index("text")]
    assert play[play.index("-l") + 1] == "pt-br"
    assert preview[-2:] == ["--", "Oi"]  # text after "--": "-5 graus" is not an option


def test_koko_receives_prepared_text(fakes, monkeypatch):
    copy = fakes / "stdin.txt"
    monkeypatch.setenv("KOKO_STDIN_COPY", str(copy))
    svc = ts.TTSService()
    assert svc.speak("Espere... sério?! Sim.", backend="kokoro", voice_id="kokoro:pm_alex", volume=50,
                     expand_abbreviations=False, normalize_numbers=False)
    _run_watch(svc)
    assert svc.state is TTSState.IDLE
    assert copy.read_text() == "Espere…\nsério?\nSim.\n"


def test_engine_crash_is_an_error_not_a_silent_stop(fakes, monkeypatch):
    # A Rust panic aborts koko with SIGABRT (exit -6). That used to be taken
    # for a stop and the request ended silently.
    monkeypatch.setenv("KOKO_MODE", "abort")
    svc = ts.TTSService()
    assert _speak(svc)
    _run_watch(svc)
    assert svc.state is TTSState.ERROR
    assert "Kokoro" in svc.last_error
    assert "panicked" in svc.last_error_detail


def test_large_text_does_not_block_the_caller(fakes, monkeypatch):
    # koko reads stdin one line at a time while it synthesizes; a text larger
    # than the pipe buffer must not block speak() (the GTK thread).
    monkeypatch.setenv("KOKO_MODE", "slow")
    monkeypatch.setenv("KOKO_PLAY_SECONDS", "0")
    copy = fakes / "stdin.txt"
    monkeypatch.setenv("KOKO_STDIN_COPY", str(copy))
    sentence = "Esta é uma frase de teste para um texto muito grande. "
    text = sentence * 6000  # ~330 KB, far above the 64 KB pipe buffer
    svc = ts.TTSService()
    start = time.monotonic()
    assert svc.speak(text, backend="kokoro", voice_id="kokoro:pm_alex", volume=50,
                     expand_abbreviations=False, normalize_numbers=False)
    assert time.monotonic() - start < 0.8  # returned before koko read anything
    _run_watch(svc, timeout=60)
    assert svc.state is TTSState.IDLE
    received = copy.read_text()
    assert received.count("Esta é uma frase de teste") == 6000  # nothing lost


def test_koko_is_started_in_its_private_workdir(fakes, monkeypatch):
    svc = ts.TTSService()
    assert _speak(svc)
    source = svc._reading.engines[0]
    output = source.args[-1]
    assert source.args[-3:-1] == ["pipe", "-o"]
    # Its own scratch file (no history here): simultaneous readings never share one.
    assert output == str(fakes / "biglinux-tts" / "koko" / f"pipe-{svc._reading.id}.wav")
    _run_watch(svc)
    assert kvs.koko_workdir() == str(fakes / "biglinux-tts" / "koko")
    assert not __import__("os").path.exists(output)  # deleted at the end
