"""Chunked download: real progress callback + cancellation."""
import importlib
import io

import pytest

kv = importlib.import_module("services.kokoro_voice_service")


class FakeResp:
    def __init__(self, data: bytes, total: int | None = None):
        self._buf = io.BytesIO(data)
        self.headers = {"Content-Length": str(total if total is not None else len(data))}
    def read(self, n=-1):
        return self._buf.read(n)
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch, data):
    monkeypatch.setattr(kv, "urlopen", lambda *a, **k: FakeResp(data))
    # Make it a "known" voice id and safe; bypass the conversion/zip by stopping early.
    monkeypatch.setitem(kv._CATALOG_BY_ID, "pf_test", object())
    # Force conversion to fail cleanly so we only exercise the download loop.
    monkeypatch.setattr(kv, "_pt_to_npy", lambda data, vid: (_ for _ in ()).throw(ValueError("stub")))


def test_progress_reported(monkeypatch):
    data = b"x" * (65536 * 3 + 10)  # >3 chunks
    _patch_urlopen(monkeypatch, data)
    seen = []
    ok, err = kv.download_voice("pf_test", progress_cb=lambda d, t: seen.append((d, t)))
    # conversion stubbed to fail → download succeeded but overall returns False
    assert seen, "progress callback must be called"
    assert seen[-1][0] == len(data), "final downloaded bytes must equal size"
    assert seen[-1][1] == len(data), "total must be reported from Content-Length"
    assert all(a <= b for (a, _), (b, _) in zip(seen, seen[1:])), "progress monotonic"


def test_cancel_stops_download(monkeypatch):
    data = b"y" * (65536 * 5)
    _patch_urlopen(monkeypatch, data)
    calls = {"n": 0}
    def cancel():
        calls["n"] += 1
        return calls["n"] > 2  # cancel after a couple of chunks
    ok, err = kv.download_voice("pf_test", cancel_check=cancel)
    assert ok is False
    assert err == "cancelled"
