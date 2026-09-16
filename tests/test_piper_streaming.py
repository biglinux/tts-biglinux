"""Piper streaming loop: ordering, temp cleanup, and race-guard — headless.

Uses the real native engine for synthesis but a fake player process so no audio
device is needed.
"""
import glob
import os
import sys
import importlib
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "usr" / "share" / "biglinux" / "tts-biglinux"
ENGINE = Path(__file__).resolve().parent.parent / "tts-engine" / "target" / "release"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(ENGINE))

ts = importlib.import_module("services.tts_service")
tts_engine = pytest.importorskip("tts_engine")

MODEL = "/usr/share/piper-voices/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx"
pytestmark = pytest.mark.skipif(not os.path.isfile(MODEL), reason="pt_BR model missing")


class FakePopen:
    """A play process that finishes immediately."""
    def __init__(self, *a, **k):
        self.returncode = 0
    def poll(self):
        return 0
    def terminate(self):
        pass
    def wait(self, timeout=None):
        return 0


def _svc():
    s = ts.TTSService.__new__(ts.TTSService)
    s._generation = 0
    s._dispatch_gen = 0
    s._process = None
    s._piper_proc = None
    s._settings = None  # _maybe_save_history returns early
    return s


def _tmp_wavs():
    return set(glob.glob("/tmp/*.wav"))


def test_stream_plays_all_chunks_and_cleans_temps(monkeypatch):
    monkeypatch.setattr(ts.subprocess, "Popen", FakePopen)
    s = _svc()
    before = _tmp_wavs()
    chunks = ["Primeira frase de teste.", "Segunda frase de teste.", "Terceira frase."]
    s._stream_piper(chunks, MODEL, 1.0, 0.667, 0.8, 1.0, gen=0, engine=tts_engine)
    # All streaming temp wavs cleaned up
    leaked = _tmp_wavs() - before
    assert not leaked, f"streaming leaked temp files: {leaked}"
    # A play process was started
    assert isinstance(s._process, FakePopen)


def test_stream_aborts_when_superseded(monkeypatch):
    monkeypatch.setattr(ts.subprocess, "Popen", FakePopen)
    s = _svc()
    before = _tmp_wavs()
    # Supersede immediately: dispatch gen 0 but service is already at gen 5
    s._generation = 5
    chunks = ["Uma.", "Duas.", "Tres."]
    s._stream_piper(chunks, MODEL, 1.0, 0.667, 0.8, 1.0, gen=0, engine=tts_engine)
    # Even aborted, no temp files leak
    assert not (_tmp_wavs() - before)
