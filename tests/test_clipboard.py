"""Selection capture: binary or badly encoded clipboard data never crashes."""
import importlib
import subprocess

cs = importlib.import_module("services.clipboard_service")


class Done:
    def __init__(self, out: bytes, rc: int = 0):
        self.stdout, self.returncode = out, rc


def test_invalid_utf8_is_decoded_leniently(monkeypatch):
    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: Done(b"ol\xe1 mundo\x00"))
    r = cs._run_capture(["wl-paste"], 0)
    assert r.success and r.text.startswith("ol") and "mundo" in r.text and "\x00" not in r.text


def test_max_chars_limit(monkeypatch):
    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: Done("á" .encode() * 50))
    assert cs._run_capture(["xsel"], 10).text == "á" * 10


def test_timeout_is_reported(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("wl-paste", 3)

    monkeypatch.setattr(cs.subprocess, "run", boom)
    assert cs._run_capture(["wl-paste"], 0).error == "timeout"


def test_wayland_asks_for_text_only(monkeypatch):
    calls = []
    monkeypatch.setattr(cs.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cs, "_run_capture", lambda args, m: calls.append(args) or cs.ClipboardResult("", False, "empty"))
    cs._get_text_wayland(0)
    assert all(a[-2:] == ["--type", "text"] for a in calls)
