"""Synthesis, streaming, history and simultaneous readings — headless.

Fake ``aplay``/``RHVoice-test``/``koko`` scripts stand in for the real
programs, so processes, threads and the watch run for real without audio.
"""
import atexit
import importlib
import os
import shutil
import stat
import tempfile
import threading
import time
from pathlib import Path

import pytest

ts = importlib.import_module("services.tts_service")
config = importlib.import_module("config")
history_service = importlib.import_module("services.history_service")
TTSState = config.TTSState

# aplay -q FILE: fails if the file is already gone (the race the streaming
# code must avoid), logs what it played, takes a moment like real playback.
FAKE_APLAY = """#!/bin/sh
f="$2"
if [ -n "$f" ]; then
  [ -f "$f" ] || { echo "missing $f" >> "$PLAYLOG"; exit 3; }
  head -c 120 "$f" | LC_ALL=C tr -dc '[:print:]' >> "$PLAYLOG"; echo >> "$PLAYLOG"
else
  cat > /dev/null
fi
sleep "${APLAY_SECONDS:-0.05}"
"""

FAKE_RHVOICE = """#!/bin/sh
cat
"""

FAKE_KOKO = """#!/bin/sh
cat >/dev/null
echo "Streaming audio for this segment..." >&2
sleep 0.2
"""


@pytest.fixture
def bindir(monkeypatch, tmp_path):
    base = Path(__file__).resolve().parent / "tmp"  # /tmp may be noexec
    base.mkdir(exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=base))
    atexit.register(shutil.rmtree, d, True)
    monkeypatch.setenv("PATH", f"{d}:{os.environ['PATH']}")
    monkeypatch.setenv("PLAYLOG", str(tmp_path / "played.log"))
    return d


def _fake(bindir: Path, name: str, script: str) -> None:
    exe = bindir / name
    exe.write_text(script)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)


def _played(tmp_path) -> list[str]:
    log = tmp_path / "played.log"
    return [ln for ln in log.read_text().splitlines() if ln] if log.exists() else []


def _pump():
    from gi.repository import GLib

    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass


def _run_watch(svc, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _pump()
        if svc._check_process() is False:
            _pump()
            return
        time.sleep(0.02)
    raise AssertionError("request did not finish")


def _fake_synth(created: list[str]):
    def synth(chunk: str) -> str:
        path = ts._temp_wav()
        Path(path).write_text(chunk)
        created.append(path)
        return path
    return synth


def _play(path: str) -> list[str]:
    return ["aplay", "-q", path]


# ── Streaming ────────────────────────────────────────────────────────

def test_stream_plays_every_chunk_in_order_and_deletes_the_files(bindir, tmp_path):
    _fake(bindir, "aplay", FAKE_APLAY)
    svc = ts.TTSService()
    created: list[str] = []
    chunks = [f"Frase numero {i}." for i in range(6)]
    svc._stream(chunks, _fake_synth(created), _play, svc._generation, svc._request_id, "fail")
    assert _played(tmp_path) == chunks  # never "missing <file>"
    assert not [p for p in created if os.path.exists(p)]
    assert svc.state is not TTSState.ERROR


def test_stream_keeps_at_most_two_chunk_files(bindir, tmp_path, monkeypatch):
    _fake(bindir, "aplay", FAKE_APLAY)
    monkeypatch.setenv("APLAY_SECONDS", "0.1")
    svc = ts.TTSService()
    created: list[str] = []
    synth = _fake_synth(created)
    peak = 0

    def counting_synth(chunk):
        nonlocal peak
        path = synth(chunk)
        peak = max(peak, sum(os.path.exists(p) for p in created))
        return path

    svc._stream([f"c{i}" for i in range(8)], counting_synth, _play, svc._generation, svc._request_id, "fail")
    assert peak <= 2


def test_stop_during_a_stream_ends_it_and_cleans_up(bindir, tmp_path, monkeypatch):
    _fake(bindir, "aplay", FAKE_APLAY)
    monkeypatch.setenv("APLAY_SECONDS", "0.3")
    svc = ts.TTSService()
    created: list[str] = []
    worker = threading.Thread(
        target=svc._stream,
        args=([f"c{i}" for i in range(20)], _fake_synth(created), _play, svc._generation, svc._request_id, "fail"),
    )
    worker.start()
    time.sleep(0.5)
    svc.stop()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert len(_played(tmp_path)) < 20
    assert not [p for p in created if os.path.exists(p)]
    assert svc._process is None


def test_superseded_stream_plays_nothing(bindir, tmp_path):
    _fake(bindir, "aplay", FAKE_APLAY)
    svc = ts.TTSService()
    created: list[str] = []
    gen = svc._generation
    svc._generation += 1  # stop() arrived before the first chunk was ready
    svc._stream(["a", "b"], _fake_synth(created), _play, gen, svc._request_id, "fail")
    assert _played(tmp_path) == []
    assert not [p for p in created if os.path.exists(p)]


def test_long_espeak_text_is_streamed_in_chunks(bindir, tmp_path):
    _fake(bindir, "aplay", FAKE_APLAY)
    svc = ts.TTSService()
    text = " ".join(f"Esta é a frase número {i} do texto longo." for i in range(60))
    assert svc.speak(text, backend="espeak-ng", voice_id="espeak-pt-br", volume=50)
    _run_watch(svc, timeout=30)
    assert svc.state is TTSState.IDLE, svc.last_error
    assert len(_played(tmp_path)) > 1  # several chunks, not one huge WAV


# ── History: every engine, only readings that were heard ──────────────

class _Saved(list):
    """History entries written (save_history_entry calls)."""

    def __init__(self):
        super().__init__()
        self.done = threading.Event()

    def __call__(self, **kw):
        self.append(kw)
        self.done.set()


@pytest.fixture
def saved(monkeypatch):
    calls = _Saved()
    monkeypatch.setattr(history_service, "save_history_entry", calls)
    return calls


def _settings(enabled=True):
    s = config.AppSettings()
    s.history.enabled = enabled
    return s


def test_rhvoice_reading_is_recorded(bindir, saved):
    _fake(bindir, "aplay", FAKE_APLAY)
    _fake(bindir, "RHVoice-test", FAKE_RHVOICE)
    svc = ts.TTSService(settings=_settings())
    assert svc.speak("Texto lido pelo RHVoice.", backend="rhvoice", voice_id="Leticia-F123", volume=50)
    _run_watch(svc)
    assert saved.done.wait(3)
    assert saved[0]["text"] == "Texto lido pelo RHVoice."
    assert saved[0]["backend"] == "rhvoice"


def test_kokoro_reading_is_recorded(bindir, saved, tmp_path, monkeypatch):
    kvs = importlib.import_module("services.kokoro_voice_service")
    _fake(bindir, "koko", FAKE_KOKO)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"x")
    voices = tmp_path / "voices.bin"
    voices.write_bytes(b"x")
    monkeypatch.setenv("KOKO_MODEL_PATH", str(model))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(kvs, "_koko_workdir_cache", None)
    monkeypatch.setattr(kvs, "get_active_voices_bin", lambda: voices)
    monkeypatch.setattr(kvs, "get_installed_voice_ids", lambda: {"pf_dora"})
    svc = ts.TTSService(settings=_settings())
    assert svc.speak("Texto do Kokoro.", backend="kokoro", voice_id="kokoro:pf_dora", volume=50)
    _run_watch(svc)
    assert saved.done.wait(3)
    assert saved[0]["backend"] == "kokoro"


def test_espeak_reading_is_recorded_with_its_audio(bindir, saved):
    _fake(bindir, "aplay", FAKE_APLAY)
    svc = ts.TTSService(settings=_settings())
    assert svc.speak("Uma frase curta.", backend="espeak-ng", voice_id="espeak-pt-br", volume=50)
    _run_watch(svc)
    assert saved.done.wait(3)
    assert saved[0]["audio_path"]  # short readings keep their WAV


def test_nothing_is_recorded_when_history_is_off_or_on_stop(bindir, saved, monkeypatch):
    _fake(bindir, "aplay", FAKE_APLAY)
    _fake(bindir, "RHVoice-test", FAKE_RHVOICE)
    svc = ts.TTSService(settings=_settings(enabled=False))
    svc.speak("Não gravar.", backend="rhvoice", voice_id="x", volume=50)
    _run_watch(svc)
    monkeypatch.setenv("APLAY_SECONDS", "2")
    svc = ts.TTSService(settings=_settings())
    svc.speak("Interrompido.", backend="rhvoice", voice_id="x", volume=50)
    time.sleep(0.2)
    svc.stop()
    _pump()
    assert not saved.done.wait(0.5)


# ── Simultaneous readings ─────────────────────────────────────────────

def test_stop_reaches_every_simultaneous_reading(bindir, monkeypatch):
    _fake(bindir, "aplay", FAKE_APLAY)
    _fake(bindir, "RHVoice-test", FAKE_RHVOICE)
    monkeypatch.setenv("APLAY_SECONDS", "5")
    svc = ts.TTSService()
    svc.speak("Primeira.", backend="rhvoice", voice_id="x", volume=50)
    first = svc._process
    svc.speak("Segunda.", backend="rhvoice", voice_id="x", volume=50, stop_previous=False)
    second = svc._process
    assert first is not second
    assert len(svc._audio_procs()) >= 2  # Pause reaches both
    svc.stop()
    assert first.wait(timeout=2) is not None and second.wait(timeout=2) is not None
    assert svc._overlapped == []
