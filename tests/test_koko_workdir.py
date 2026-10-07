"""koko (Kokoro binary) writes tmp/pipe_output.wav relative to its cwd.

Installed, the app runs from the root-owned /usr/share/biglinux/tts-biglinux,
so koko must be started in a private writable directory or it exits with
"Permission denied" and nothing is heard.
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


def test_koko_is_started_in_the_writable_workdir(tmp_path, monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    tts = importlib.import_module("services.tts_service")
    monkeypatch.setattr(ts, "koko_problem", lambda voice_id, blend="": "")
    monkeypatch.setattr(ts.shutil, "which", lambda name: "/usr/bin/koko")

    seen = {}

    class FakeProc:
        stdin = None
        stderr = None

    def fake_popen(cmd, **kwargs):
        seen.update(kwargs, cmd=cmd)
        return FakeProc()

    monkeypatch.setattr(tts.subprocess, "Popen", fake_popen)
    svc = tts.TTSService()
    assert svc._speak_kokoro_koko("Olá", "kokoro:pm_alex", 0, 0, 50)
    workdir = str(tmp_path / "biglinux-tts" / "koko")
    assert seen["cmd"][0] == "/usr/bin/koko"
    assert seen["cwd"] == workdir
    # The scratch WAV is written there too, never relative to the app dir.
    assert seen["cmd"][-2:] == ["-o", workdir + "/pipe_output.wav"]
