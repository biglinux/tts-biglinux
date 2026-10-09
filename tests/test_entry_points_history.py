"""Every way of starting a reading ends up in the History (real app object).

Global shortcut / tray (both run TTSApplication._on_tray_speak, which captures
the selection), the media "Play" key (replay_last), the History's "Read
again", and the queue playback mode. Fake engines: no sound. Needs a display
(the application object loads GTK); skipped otherwise.
"""
import importlib
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display for GTK", allow_module_level=True)

from fake_engines import use_fakes  # noqa: E402

application = importlib.import_module("application")
clipboard = importlib.import_module("services.clipboard_service")
hs = importlib.import_module("services.history_service")


def _pump(condition, timeout=10.0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.iteration(False):
            pass
        if condition():
            return True
        time.sleep(0.02)
    return condition()


@pytest.fixture
def app(tmp_path, monkeypatch):
    use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(application.TTSApplication, "_show_dbus_notification", lambda *a, **k: None)
    a = application.TTSApplication()
    s = a.settings
    s.history.enabled = True
    s.speech.backend = "rhvoice"
    s.speech.voice_id = "Leticia-F123"
    return a


def _select(monkeypatch, *texts):
    queue = list(texts)
    monkeypatch.setattr(clipboard, "get_selected_text",
                        lambda max_chars=0: clipboard.ClipboardResult(queue.pop(0), True, ""))


def _texts(n):
    _pump(lambda: hs.get_history_dir().exists() and hs.count_history_entries() >= n)
    return [e["text"] for e in hs.load_history_entries()]


def test_shortcut_and_tray_reading_is_recorded(app, monkeypatch):
    _select(monkeypatch, "Texto selecionado em outro aplicativo.")
    app._on_tray_speak()
    assert _texts(1) == ["Texto selecionado em outro aplicativo."]
    [entry] = hs.load_history_entries()
    assert entry["backend"] == "rhvoice" and entry["has_audio"]


def test_media_play_key_reads_again_and_records_again(app, monkeypatch):
    _select(monkeypatch, "Uma vez.")
    app._on_tray_speak()
    _texts(1)
    _pump(lambda: not app.tts_service.is_speaking)
    assert app.replay_last()
    assert _texts(2) == ["Uma vez.", "Uma vez."]


def test_queue_mode_records_each_reading(app, monkeypatch):
    monkeypatch.setenv("APLAY_SECONDS", "0.4")
    app.settings.history.playback_mode = "queue"
    _select(monkeypatch, "Primeira da fila.", "Segunda da fila.")
    app._on_tray_speak()
    _pump(lambda: app.tts_service.is_speaking)
    app._on_tray_speak()  # while the first is still playing: queued
    assert _texts(2) == ["Segunda da fila.", "Primeira da fila."]


def test_saving_off_records_nothing_from_the_shortcut(app, monkeypatch):
    app.settings.history.enabled = False
    _select(monkeypatch, "Não guardar.")
    app._on_tray_speak()
    _pump(lambda: app.tts_service.state.value == "speaking", timeout=3)
    _pump(lambda: not app.tts_service.is_speaking, timeout=5)
    time.sleep(0.3)
    assert not hs.get_history_dir().exists() or hs.count_history_entries() == 0
