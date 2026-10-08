"""Race guard: audio of a stopped request never starts, simultaneous readings coexist.

stop() bumps the request generation; speak() must not (simultaneous mode
keeps earlier readings alive). Real processes stand in for the players.
"""
import importlib
import subprocess

from fake_engines import use_fakes

ts = importlib.import_module("services.tts_service")


def _reading(svc, text="x"):
    reading = ts._Reading(id=text, text=text, processed=text, backend="espeak-ng", voice_id="", gen=svc._generation)
    svc._reading = reading
    return reading


def _sleeper():
    return subprocess.Popen(["sleep", "5"])


def test_player_of_a_stopped_request_is_killed_at_once(tmp_path, monkeypatch):
    use_fakes(monkeypatch, tmp_path)
    svc = ts.TTSService()
    reading = _reading(svc)
    svc.stop()  # arrives while the worker was still synthesizing
    player = _sleeper()
    assert svc._begin_playback(reading, player) is False
    assert player.wait(timeout=2) is not None  # never left playing
    assert not reading.audio_started


def test_speak_without_stop_keeps_earlier_readings_current(tmp_path, monkeypatch):
    use_fakes(monkeypatch, tmp_path)
    svc = ts.TTSService()
    first = _reading(svc, "a")
    p1, p2 = _sleeper(), _sleeper()
    assert svc._begin_playback(first, p1)
    svc._overlap_current()  # simultaneous mode: no stop()
    second = _reading(svc, "b")
    assert first.gen == second.gen == svc._generation
    assert svc._overlapped == [first]
    assert svc._begin_playback(first, _sleeper())  # its next chunk still plays
    assert svc._begin_playback(second, p2)
    assert svc._process is p2  # the newest reading owns the controls
    svc.stop()
    assert p1.wait(timeout=2) is not None and p2.wait(timeout=2) is not None


def test_a_finished_reading_never_starts_a_player(tmp_path, monkeypatch):
    use_fakes(monkeypatch, tmp_path)
    svc = ts.TTSService()
    reading = _reading(svc)
    svc._overlap_current()  # nothing alive: finished on the spot
    player = _sleeper()
    assert svc._begin_playback(reading, player) is False
    assert player.wait(timeout=2) is not None
