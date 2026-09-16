"""Kokoro voices.bin atomicity + id sanitization regression tests."""
import importlib
import zipfile

import pytest

kv = importlib.import_module("services.kokoro_voice_service")


@pytest.fixture
def user_bin(tmp_path, monkeypatch):
    d = tmp_path / "kokoro-voices"
    monkeypatch.setattr(kv, "USER_VOICES_DIR", d)
    monkeypatch.setattr(kv, "USER_VOICES_BIN", d / "voices.bin")
    return d


def test_add_voices_accumulate_no_loss(user_bin):
    kv._add_voice_to_zip("af_one", b"AAAA")
    kv._add_voice_to_zip("af_two", b"BBBB")
    with zipfile.ZipFile(kv.USER_VOICES_BIN) as z:
        names = set(z.namelist())
    assert {"af_one.npy", "af_two.npy"} <= names
    # No leftover temp .part files
    assert not list(user_bin.glob("*.part"))


def test_atomic_write_preserves_old_on_failure(user_bin, monkeypatch):
    kv._add_voice_to_zip("af_keep", b"KEEP")
    original = kv.USER_VOICES_BIN.read_bytes()

    # Force a failure during the ZIP write; the old file must remain intact.
    real_zip = kv.zipfile.ZipFile

    class Boom(Exception):
        pass

    def failing_zip(f, mode="r", *a, **k):
        if mode == "w":
            raise Boom("simulated crash mid-write")
        return real_zip(f, mode, *a, **k)

    monkeypatch.setattr(kv.zipfile, "ZipFile", failing_zip)
    with pytest.raises(Boom):
        kv._add_voice_to_zip("af_new", b"NEW")

    assert kv.USER_VOICES_BIN.read_bytes() == original, "old voices.bin must survive a failed write"
    assert not list(user_bin.glob("*.part")), "no temp file left behind"


def test_voice_id_sanitization():
    assert kv._RE_SAFE_VOICE_ID.match("pf_dora")
    assert not kv._RE_SAFE_VOICE_ID.match("../etc/passwd")
    assert not kv._RE_SAFE_VOICE_ID.match("a/b")
    assert not kv._RE_SAFE_VOICE_ID.match("a.npy")
