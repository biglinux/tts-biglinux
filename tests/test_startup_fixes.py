"""Startup and desktop-integration regressions (review of 2026-11-02)."""
import subprocess
from types import SimpleNamespace

import config
from services import clipboard_service, settings_service
from services.desktop_integration_service import DesktopIntegrationService as DIS

# ── settings ─────────────────────────────────────────────────────────


def test_infinity_in_settings_does_not_crash():
    s = config._deserialize_settings({"speech": {"rate": float("inf"), "volume": 1e999}})
    assert s.speech.rate == config.RATE_DEFAULT


def test_corrupt_settings_are_kept_and_history_stays_off():
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_FILE.write_text("{not json")

    s = config.load_settings()

    assert s.history.enabled is False
    assert config.SETTINGS_FILE.with_suffix(".json.corrupt").read_text() == "{not json"


def test_unreadable_settings_do_not_crash():
    config.SETTINGS_FILE.mkdir(parents=True)  # a directory: read_text raises OSError
    assert isinstance(config.load_settings(), config.AppSettings)


def test_restore_defaults_keeps_the_shared_object():
    svc = settings_service.SettingsService()
    shared = svc.get()  # what TTSService and the History hold
    shared.history.enabled = False
    shared.speech.rate = 40

    returned = svc.reset_to_defaults()

    assert returned is shared
    assert shared.history.enabled is True
    assert shared.speech.rate == config.AppSettings().speech.rate


# ── shortcut on other desktops ──────────────────────────────────────


def test_unknown_desktop_does_not_claim_a_shortcut(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "Hyprland")
    monkeypatch.setenv("DESKTOP_SESSION", "hyprland")
    called = []
    monkeypatch.setattr(DIS, "register_gnome_shortcut", classmethod(lambda *a: called.append(a) or True))

    assert DIS.register_shortcut_for_current_de("<Alt>v") is False
    assert called == []


class FakeXfconf:
    def __init__(self, bindings):
        self.bindings = dict(bindings)
        self.calls = []

    def run(self, args, **kwargs):
        self.calls.append(args)
        if "-l" in args:
            out = "".join(f"{k}  {v}\n" for k, v in self.bindings.items())
            return SimpleNamespace(returncode=0, stdout=out)
        prop = args[args.index("-p") + 1]
        if "-r" in args:
            self.bindings.pop(prop, None)
        else:
            self.bindings[prop] = args[args.index("-s") + 1]
        return SimpleNamespace(returncode=0, stdout="")


def _xfce(monkeypatch, bindings):
    fake = FakeXfconf(bindings)
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    return fake


def test_xfce_keeps_users_own_binding_to_the_app(monkeypatch):
    fake = _xfce(monkeypatch, {
        "/commands/custom/<Super>t": "/usr/bin/biglinux-tts",
        "/commands/custom/<Alt>s": "/usr/bin/biglinux-tts-speak",
    })

    assert DIS.register_xfce_shortcut("<Alt>v", "/usr/bin/biglinux-tts-speak")

    assert fake.bindings == {
        "/commands/custom/<Super>t": "/usr/bin/biglinux-tts",
        "/commands/custom/<Alt>v": "/usr/bin/biglinux-tts-speak",
    }


def test_xfce_sets_new_binding_before_removing_old(monkeypatch):
    fake = _xfce(monkeypatch, {"/commands/custom/<Alt>s": "/usr/bin/biglinux-tts-speak"})

    DIS.register_xfce_shortcut("<Alt>v", "/usr/bin/biglinux-tts-speak")

    writes = [c for c in fake.calls if "-l" not in c]
    assert "-n" in writes[0] and "-r" in writes[1]


def test_xfce_does_not_overwrite_another_command(monkeypatch):
    fake = _xfce(monkeypatch, {"/commands/custom/<Alt>v": "xfce4-terminal"})

    assert DIS.register_xfce_shortcut("<Alt>v", "/usr/bin/biglinux-tts-speak") is False
    assert fake.bindings["/commands/custom/<Alt>v"] == "xfce4-terminal"


def test_xfce_already_right_writes_nothing(monkeypatch):
    fake = _xfce(monkeypatch, {"/commands/custom/<Alt>v": "/usr/bin/biglinux-tts-speak"})

    assert DIS.register_xfce_shortcut("<Alt>v", "/usr/bin/biglinux-tts-speak")
    assert all("-l" in c for c in fake.calls)


# ── selection capture on X11 ────────────────────────────────────────


def test_x11_falls_back_to_the_clipboard(monkeypatch):
    seen = []

    def capture(args, max_chars):
        seen.append(args)
        return clipboard_service.ClipboardResult("", False, "empty")

    monkeypatch.setattr(clipboard_service, "_run_capture", capture)
    monkeypatch.setattr(clipboard_service.shutil, "which", lambda n: n == "xsel")

    clipboard_service._get_text_x11(100)

    assert ["xsel", "--clipboard", "-o"] in seen


# ── tray helper that gives up ───────────────────────────────────────


def test_tray_helper_error_is_read_and_reported_on_exit(caplog):
    import os

    from gi.repository import GLib

    from services.tray_service import TrayIcon

    tray = TrayIcon(title="t", tooltip="t")
    exits = []
    tray.on_exit = lambda: exits.append(1) or False
    r, w = os.pipe()
    os.write(w, b'{"event": "error", "message": "No system tray"}\n')
    os.close(w)  # the helper exited: IN and HUP arrive together
    channel = GLib.IOChannel.unix_new(r)
    channel.set_encoding(None)
    with caplog.at_level("ERROR"):
        keep = tray._on_child_output(channel, GLib.IOCondition.IN | GLib.IOCondition.HUP)
    os.close(r)
    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass

    assert keep is False
    assert "No system tray" in caplog.text
    assert exits == [1]
