"""koko (Kokoro binary) writes scratch files relative to its cwd.

Installed, the app runs from the root-owned /usr/share/biglinux/tts-biglinux,
so koko must be started in a private writable directory or it exits with
"Permission denied" and nothing is heard (tested with a real process in
test_tts_lifecycle.test_koko_is_started_in_its_private_workdir).
"""
import importlib
import os
import stat

ts = importlib.import_module("services.kokoro_voice_service")


def _reset(monkeypatch):
    monkeypatch.setattr(ts, "_koko_workdir_cache", None)


def test_workdir_is_private_and_writable_in_runtime_dir(tmp_path, monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = ts.koko_workdir()
    assert path == str(tmp_path / "biglinux-tts" / "koko")
    assert os.access(path, os.W_OK)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o700


def test_workdir_falls_back_when_runtime_dir_is_unusable(tmp_path, monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", None)
    path = ts.koko_workdir()
    assert os.path.dirname(path) == str(tmp_path)
    assert os.access(path, os.W_OK)
