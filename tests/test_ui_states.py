"""The "Ready to speak" card, engine status and History menu against real
widgets (GTK 4 / libadwaita). Skipped when no display is available — run
under a headless backend, e.g. ``GDK_BACKEND=broadway`` with gtk4-broadwayd.
"""
import importlib

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

if not Gtk.init_check():
    pytest.skip("no display for GTK", allow_module_level=True)
Adw.init()

config = importlib.import_module("config")
mv = importlib.import_module("ui.main_view")
ts = importlib.import_module("services.tts_service")
ss = importlib.import_module("services.shortcut_service")
vm = importlib.import_module("services.voice_manager")
TTSState = config.TTSState


class FakeSettingsService:
    def __init__(self):
        self.settings = config.AppSettings()
        self.saved = 0

    def get(self):
        return self.settings

    def save(self, *_a):
        self.saved += 1

    def save_now(self, *_a):
        self.saved += 1


@pytest.fixture
def view(monkeypatch):
    # No real discovery: the test feeds a catalog itself.
    monkeypatch.setattr(mv, "run_in_thread", lambda *a, **k: None)
    svc = ts.TTSService()
    v = mv.MainView(tts_service=svc, settings_service=FakeSettingsService(), on_toast=lambda *a: None)
    return v, svc


def _classes(widget):
    return set(widget.get_css_classes())


def test_card_follows_every_request_state(view):
    v, svc = view
    v._render_hero(TTSState.IDLE)
    assert v._hero_title.get_label() == mv._("Ready to speak")
    assert "suggested-action" in _classes(v._test_button)
    assert not v._hero_actions.get_visible()

    v._render_hero(TTSState.LOADING)
    assert v._hero_badge.get_visible_child_name() == "spinner"
    assert {"destructive-action"} <= _classes(v._test_button)
    assert "suggested-action" not in _classes(v._test_button)

    v._render_hero(TTSState.SPEAKING)
    assert "speaking" in _classes(v._hero_badge)
    assert "suggested-action" not in _classes(v._test_button)

    svc._error_message, svc._error_action, svc._error_detail = "Kokoro failed", "voice-manager", "trace"
    v._render_hero(TTSState.ERROR)
    assert v._hero_subtitle.get_label() == "Kokoro failed"  # the real reason
    assert v._hero_actions.get_visible() and v._hero_manage.get_visible()
    assert not v._hero_retry.get_visible() and v._hero_details.get_visible()
    assert "suggested-action" in _classes(v._test_button)
    assert "destructive-action" not in _classes(v._test_button)


def test_stopped_is_shown_after_a_user_stop(view):
    v, svc = view
    v._render_hero(TTSState.SPEAKING)
    svc._stopped_by_user = True
    v._on_tts_state_changed(TTSState.IDLE)
    assert v._hero_title.get_label() == mv._("Stopped")


def test_shortcut_keys_and_failure_note(view):
    v, _svc = view
    v._settings.shortcut.keybinding = "<Control><Alt>t"
    v.on_shortcut_status(ss.ShortcutStatus(accel="<Control><Alt>t", registered=False, message="taken", conflicts=["Terminal"]))
    labels = [c.get_label() for c in _children(v._hero_keys) if "keycap" in _classes(c)]
    assert labels == ["Ctrl", "Alt", "T"]
    assert v._hero_shortcut_note.get_visible()
    v.on_shortcut_status(ss.ShortcutStatus(accel="<Control><Alt>t", registered=True))
    assert not v._hero_shortcut_note.get_visible()


def test_conflicting_new_shortcut_is_reverted(view, monkeypatch):
    v, _svc = view
    applied = []

    class App:
        def apply_shortcut(self, accel):
            applied.append(accel)

    monkeypatch.setattr(v, "_root_app", lambda: App())
    v._settings.shortcut.keybinding = "<Control><Alt>t"
    v._pending_shortcut = ("<Control><Alt>t", "<Alt>v")
    v.on_shortcut_status(ss.ShortcutStatus(accel="<Control><Alt>t", registered=False, message="taken", conflicts=["Terminal"]))
    assert v._settings.shortcut.keybinding == "<Alt>v"
    assert applied == ["<Alt>v"]


def test_engine_status_reflects_catalog_and_activity(view):
    v, svc = view
    cat = vm.VoiceCatalog()
    cat.voices = [vm.VoiceInfo(voice_id="kokoro:pm_alex", name="Alex", language="pt-BR", language_name="Português", backend="kokoro")]
    cat.engines = {
        "rhvoice": vm.EngineAvailability.NOT_INSTALLED,
        "espeak-ng": vm.EngineAvailability.NO_VOICES,
        "piper": vm.EngineAvailability.NOT_INSTALLED,
        "kokoro": vm.EngineAvailability.READY,
    }
    v._catalog = cat
    v._settings.speech.backend = "kokoro"
    v._refresh_engine_status()
    pill = lambda b: v._engine_rows[b][1].get_label()  # noqa: E731
    assert pill("rhvoice") == mv._("Not installed")
    assert pill("espeak-ng") == mv._("No voices")
    assert pill("kokoro") == mv._("Ready")
    svc._state = TTSState.LOADING
    v._refresh_engine_status()
    assert pill("kokoro") == mv._("Loading")


def test_saved_voice_in_first_position_is_kept(view):
    v, _svc = view
    cat = vm.VoiceCatalog()
    cat.voices = [
        vm.VoiceInfo(voice_id="kokoro:af_heart", name="Heart", language="en-US", language_name="A-English", backend="kokoro"),
        vm.VoiceInfo(voice_id="kokoro:pm_alex", name="Alex", language="pt-BR", language_name="B-Português", backend="kokoro"),
    ]
    cat.backends_available = ["kokoro"]
    v._settings.speech.backend = "kokoro"
    v._settings.speech.voice_id = "kokoro:af_heart"  # sorts first
    v._on_voices_discovered(cat)
    assert v._settings.speech.voice_id == "kokoro:af_heart"


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def test_sidebar_is_shown_in_wide_windows_and_collapses_when_narrow():
    """Regression: binding the toggle hid the sidebar at startup."""
    window = importlib.import_module("window")
    split, button = window.create_sidebar_split()
    assert split.get_show_sidebar()  # wide window: sidebar visible
    button.set_active(False)  # narrow window, settings closed
    assert not split.get_show_sidebar()
    split.set_show_sidebar(True)  # e.g. swipe / breakpoint unapply
    assert button.get_active()
