"""Synthesis, streaming and the reading history — headless, real processes.

The fake programs of tests/fake_engines.py replace aplay, RHVoice-test and
koko; espeak-ng runs for real (no sound: the player is fake).
"""
import importlib
import os
import threading
import time
from pathlib import Path

import pytest

from fake_engines import played, text_wav, use_fakes, wav_seconds

ts = importlib.import_module("services.tts_service")
config = importlib.import_module("config")
hs = importlib.import_module("services.history_service")
TTSState = config.TTSState


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(hs, "_MUSIC_DIR", tmp_path / "music")
    return tmp_path


def _pump():
    from gi.repository import GLib

    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass


def _run_watch(svc, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _pump()
        if svc._check_process() is False:
            _pump()
            return
        time.sleep(0.02)
    raise AssertionError("request did not finish")


def _settings(enabled=True, save_audio=True, save_text=True):
    s = config.AppSettings()
    s.history.enabled = enabled
    s.history.save_audio = save_audio
    s.history.save_text = save_text
    return s


def _entries(timeout=5.0, expected=1):
    """History entries, waiting for the background saves."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rows = hs.load_history_entries() if hs.get_history_dir().exists() else []
        if len(rows) >= expected:
            return rows
        time.sleep(0.05)
    return hs.load_history_entries() if hs.get_history_dir().exists() else []


def _reading(svc, text="x", backend="espeak-ng"):
    reading = ts._Reading(id="t", text=text, processed=text, backend=backend, voice_id="", gen=svc._generation)
    svc._reading = reading
    return reading


def _chunk_synth(tmp_path: Path, created: list[str]):
    def synth(chunk: str) -> str:
        path = text_wav(Path(ts._temp_wav()), chunk)
        created.append(path)
        return path
    return synth


def _play(path: str) -> list[str]:
    return ["aplay", "-q", path]


# ── Streaming ────────────────────────────────────────────────────────

def test_stream_plays_every_chunk_in_order_and_deletes_the_files(fakes):
    svc = ts.TTSService()
    created: list[str] = []
    chunks = [f"Frase numero {i}." for i in range(6)]
    svc._stream(_reading(svc), chunks, _chunk_synth(fakes, created), _play, "fail")
    assert played(fakes) == chunks  # never "missing <file>"
    assert not [p for p in created if os.path.exists(p)]
    assert svc.state is not TTSState.ERROR


def test_stream_keeps_at_most_two_chunk_files(fakes, monkeypatch):
    monkeypatch.setenv("APLAY_SECONDS", "0.1")
    svc = ts.TTSService()
    created: list[str] = []
    synth = _chunk_synth(fakes, created)
    peak = 0

    def counting_synth(chunk):
        nonlocal peak
        path = synth(chunk)
        peak = max(peak, sum(os.path.exists(p) for p in created))
        return path

    svc._stream(_reading(svc), [f"c{i}" for i in range(8)], counting_synth, _play, "fail")
    assert peak <= 2


def test_stop_during_a_stream_ends_it_and_cleans_up(fakes, monkeypatch):
    monkeypatch.setenv("APLAY_SECONDS", "0.3")
    svc = ts.TTSService()
    created: list[str] = []
    worker = threading.Thread(
        target=svc._stream,
        args=(_reading(svc), [f"c{i}" for i in range(20)], _chunk_synth(fakes, created), _play, "fail"),
    )
    worker.start()
    time.sleep(0.5)
    svc.stop()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert len(played(fakes)) < 20
    assert not [p for p in created if os.path.exists(p)]
    assert svc._process is None


def test_superseded_stream_plays_nothing(fakes):
    svc = ts.TTSService()
    created: list[str] = []
    reading = _reading(svc)
    svc._generation += 1  # stop() arrived before the first chunk was ready
    svc._stream(reading, ["a", "b"], _chunk_synth(fakes, created), _play, "fail")
    assert played(fakes) == []
    assert not [p for p in created if os.path.exists(p)]


def test_long_espeak_text_is_streamed_in_chunks(fakes):
    svc = ts.TTSService()
    text = " ".join(f"Esta é a frase número {i} do texto longo." for i in range(60))
    assert svc.speak(text, backend="espeak-ng", voice_id="espeak-pt-br", volume=50)
    _run_watch(svc, timeout=30)
    assert svc.state is TTSState.IDLE, svc.last_error
    assert len(played(fakes)) > 1  # several chunks, not one huge WAV


# ── History: every engine, with the audio that was played ─────────────

@pytest.mark.parametrize(
    ("backend", "voice"),
    [("rhvoice", "Leticia-F123"), ("espeak-ng", "espeak-pt-br"), ("kokoro", "kokoro:pf_dora")],
)
def test_every_engine_records_text_and_audio(fakes, backend, voice):
    svc = ts.TTSService(settings=_settings())
    assert svc.speak("Uma frase. Outra frase.", backend=backend, voice_id=voice, volume=50)
    _run_watch(svc)
    [entry] = _entries()
    assert entry["backend"] == backend and entry["voice_id"] == voice
    assert entry["text"] == "Uma frase. Outra frase."
    assert entry["processed_text"]
    assert entry["status"] == "completed"
    audio = hs.audio_path(entry)
    assert audio is not None and entry["duration"] > 0
    assert abs(wav_seconds(audio) - entry["duration"]) < 0.01  # header fixed
    assert not list(hs.get_history_dir().glob(".rec-*"))  # no leftover recording


def test_kokoro_history_audio_is_exactly_what_was_played(fakes):
    svc = ts.TTSService(settings=_settings())
    assert svc.speak("Primeira. Segunda. Terceira.", backend="kokoro", voice_id="kokoro:pf_dora", volume=50)
    _run_watch(svc)
    [entry] = _entries()
    # The fake koko writes 0.1 s per line; three sentences = three lines.
    assert entry["duration"] == pytest.approx(0.3, abs=0.001)


def test_kokoro_history_audio_is_valid_float_audio(fakes):
    # koko stream mixed its log lines into the audio: the samples were
    # garbage (huge values, NaN) and nothing was heard. koko pipe -o writes
    # clean audio; the history keeps that file, with its header fixed.
    import struct

    svc = ts.TTSService(settings=_settings())
    assert svc.speak("Uma frase. Outra frase.", backend="kokoro", voice_id="kokoro:pf_dora", volume=50)
    _run_watch(svc)
    [entry] = _entries()
    data = hs.audio_path(entry).read_bytes()
    assert data[:4] == b"RIFF" and struct.unpack("<I", data[4:8])[0] == len(data) - 8
    assert struct.unpack("<H", data[20:22])[0] == 3  # float32, as koko writes it
    assert struct.unpack("<I", data[40:44])[0] == len(data) - 44
    samples = struct.unpack(f"<{(len(data) - 44) // 4}f", data[44:])
    assert samples and all(abs(v) <= 1.0 for v in samples)


def test_stopped_kokoro_keeps_only_what_was_heard(fakes, monkeypatch):
    # koko synthesizes ahead of playback: its file holds more than was heard.
    monkeypatch.setenv("KOKO_PLAY_SECONDS", "0.5")
    svc = ts.TTSService(settings=_settings())
    text = " ".join(f"Frase {i}." for i in range(8))
    assert svc.speak(text, backend="kokoro", voice_id="kokoro:pf_dora", volume=50)
    assert _pump_until(lambda: svc.state is TTSState.SPEAKING)
    time.sleep(0.3)
    svc.stop()
    [entry] = _entries()
    assert entry["status"] == "stopped"
    assert 0.2 <= entry["duration"] <= 0.5
    assert abs(wav_seconds(hs.audio_path(entry)) - entry["duration"]) < 0.01


def test_paused_time_is_not_counted_as_heard(fakes, monkeypatch):
    monkeypatch.setenv("KOKO_PLAY_SECONDS", "0.5")
    svc = ts.TTSService(settings=_settings())
    text = " ".join(f"Frase {i}." for i in range(8))
    assert svc.speak(text, backend="kokoro", voice_id="kokoro:pf_dora", volume=50)
    assert _pump_until(lambda: svc.state is TTSState.SPEAKING)
    time.sleep(0.2)
    assert svc.pause()
    time.sleep(0.6)
    assert svc.resume()
    time.sleep(0.1)
    svc.stop()
    [entry] = _entries()
    assert 0.2 <= entry["duration"] <= 0.45  # ~0.3 s heard, not ~0.9 s


def test_long_streamed_reading_keeps_all_its_chunks(fakes):
    svc = ts.TTSService(settings=_settings())
    text = " ".join(f"Frase número {i} de um texto bem longo para o teste." for i in range(40))
    assert svc.speak(text, backend="espeak-ng", voice_id="espeak-pt-br", volume=50)
    _run_watch(svc, timeout=30)
    [entry] = _entries()
    assert len(played(fakes)) > 1
    assert entry["duration"] > 20  # every chunk, not only the last one
    assert entry["text"] == text


def test_stopped_reading_is_recorded_as_stopped(fakes, monkeypatch):
    monkeypatch.setenv("APLAY_SECONDS", "3")
    svc = ts.TTSService(settings=_settings())
    assert svc.speak("Uma frase que será interrompida.", backend="rhvoice", voice_id="x", volume=50)
    end = time.monotonic() + 3
    while svc.state is not TTSState.SPEAKING and time.monotonic() < end:
        _pump()
        time.sleep(0.02)
    svc.stop()
    [entry] = _entries()
    assert entry["status"] == "stopped"


def test_failed_reading_after_sound_is_recorded_as_error(fakes):
    svc = ts.TTSService(settings=_settings())
    reading = _reading(svc, "Texto")
    reading.audio_started = True
    svc._fail("boom")
    [entry] = _entries()
    assert entry["status"] == "error"


def test_nothing_heard_nothing_recorded(fakes, monkeypatch):
    monkeypatch.setenv("KOKO_MODE", "fail")
    svc = ts.TTSService(settings=_settings())
    assert svc.speak("Falha.", backend="kokoro", voice_id="kokoro:pf_dora", volume=50)
    _run_watch(svc)
    assert svc.state is TTSState.ERROR
    assert _entries(timeout=0.5) == []


def test_history_off_records_nothing_and_on_again_records(fakes):
    settings = _settings(enabled=False)
    svc = ts.TTSService(settings=settings)
    svc.speak("Não gravar.", backend="rhvoice", voice_id="x", volume=50)
    _run_watch(svc)
    assert _entries(timeout=0.5) == []
    assert not list(hs.get_history_dir().glob(".rec-*")) if hs.get_history_dir().exists() else True
    settings.history.enabled = True
    svc.speak("Gravar.", backend="rhvoice", voice_id="x", volume=50)
    _run_watch(svc)
    assert [e["text"] for e in _entries()] == ["Gravar."]


def test_text_only_and_audio_only(fakes):
    svc = ts.TTSService(settings=_settings(save_audio=False))
    svc.speak("Só o texto.", backend="rhvoice", voice_id="x", volume=50)
    _run_watch(svc)
    [entry] = _entries()
    assert entry["text"] == "Só o texto." and not entry["has_audio"]


def test_simultaneous_readings_are_separate_entries(fakes, monkeypatch):
    monkeypatch.setenv("APLAY_SECONDS", "1")
    svc = ts.TTSService(settings=_settings())
    svc.speak("Primeira leitura.", backend="rhvoice", voice_id="x", volume=50)
    time.sleep(0.3)
    svc.speak("Segunda leitura.", backend="rhvoice", voice_id="x", volume=50, stop_previous=False)
    assert len(svc._overlapped) == 1
    assert len(svc._audio_procs()) >= 2  # Pause reaches both
    _run_watch(svc)
    rows = _entries(expected=2)
    assert sorted(e["text"] for e in rows) == ["Primeira leitura.", "Segunda leitura."]
    assert all(e["status"] == "completed" and e["has_audio"] for e in rows)
    assert len({e["audio_file"] for e in rows}) == 2  # never the same file


def test_stop_reaches_every_simultaneous_reading(fakes, monkeypatch):
    monkeypatch.setenv("APLAY_SECONDS", "5")
    svc = ts.TTSService()
    svc.speak("Primeira.", backend="rhvoice", voice_id="x", volume=50)
    first = svc._process
    svc.speak("Segunda.", backend="rhvoice", voice_id="x", volume=50, stop_previous=False)
    second = svc._process
    assert first is not second
    svc.stop()
    assert first.wait(timeout=2) is not None and second.wait(timeout=2) is not None
    assert svc._overlapped == []


def test_readings_in_sequence_each_have_an_entry(fakes):
    svc = ts.TTSService(settings=_settings())
    for i in range(3):
        svc.speak(f"Leitura {i}.", backend="rhvoice", voice_id="x", volume=50)
        _run_watch(svc)
    assert [e["text"] for e in _entries(expected=3)] == ["Leitura 2.", "Leitura 1.", "Leitura 0."]


def test_history_folder_without_permission_never_breaks_reading(fakes, monkeypatch):
    # No history folder possible: its parent is a file (root-proof, unlike a
    # read-only folder — CI runs as root).
    blocker = fakes / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(hs, "_MUSIC_DIR", blocker / "Music")
    svc = ts.TTSService(settings=_settings())
    assert svc.speak("Ainda assim leio.", backend="rhvoice", voice_id="x", volume=50)
    _run_watch(svc)
    assert svc.state is TTSState.IDLE  # the reading itself is fine
    assert played(fakes)
    assert not hs.get_history_dir().exists()


@pytest.mark.parametrize(("backend", "voice"), [("rhvoice", "x"), ("kokoro", "kokoro:pf_dora")])
def test_pause_freezes_and_resume_finishes_the_reading(fakes, monkeypatch, backend, voice):
    monkeypatch.setenv("APLAY_SECONDS", "0.6")
    monkeypatch.setenv("KOKO_PLAY_SECONDS", "0.6")
    svc = ts.TTSService(settings=_settings())
    assert svc.speak("Frase pausada.", backend=backend, voice_id=voice, volume=50)
    assert _pump_until(lambda: svc.state is TTSState.SPEAKING)
    assert svc.pause()
    time.sleep(1.2)  # longer than the whole reading: it must not end while paused
    _pump()
    assert svc._check_process() is True and svc.is_paused
    assert svc.resume()
    _run_watch(svc)
    assert svc.state is TTSState.IDLE
    [entry] = _entries()
    assert entry["status"] == "completed"


def _pump_until(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _pump()
        if condition():
            return True
        time.sleep(0.02)
    return False
