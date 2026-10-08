"""
Main application window for BigLinux TTS.

Implements Adw.ApplicationWindow with header, navigation, and main view.
"""

# ruff: noqa: E402  # gi.require_version must run before repository imports.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from config import (
    APP_NAME,
    WINDOW_HEIGHT_MIN,
    WINDOW_WIDTH_MIN,
    save_settings,
)
from ui.history_view import HistoryView
from ui.main_view import MainView
from ui.welcome_dialog import WelcomeDialog
from utils.i18n import _

if TYPE_CHECKING:
    from application import TTSApplication
    from config import TTSState

logger = logging.getLogger(__name__)


def create_sidebar_split() -> tuple[Adw.OverlaySplitView, Gtk.ToggleButton]:
    """Sidebar/content split plus the header toggle used when it collapses."""
    split = Adw.OverlaySplitView()
    split.set_vexpand(True)
    split.set_min_sidebar_width(280)
    split.set_max_sidebar_width(340)
    split.set_sidebar_width_fraction(0.34)

    button = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
    button.set_tooltip_text(_("Settings"))
    button.update_property([Gtk.AccessibleProperty.LABEL], [_("Show settings")])
    button.set_visible(False)  # only while collapsed (breakpoint setter)
    # Starts active: SYNC_CREATE copies this to show-sidebar, and wide
    # windows must show the sidebar.
    button.set_active(True)
    button.bind_property(
        "active", split, "show-sidebar",
        GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
    )
    return split, button


class TTSWindow(Adw.ApplicationWindow):
    """
    Main window with header bar, navigation, and settings view.

    Follows GNOME HIG and Adwaita patterns.
    """

    def __init__(self, application: TTSApplication) -> None:
        super().__init__(application=application)

        self._app = application

        # Window state tracking
        self._size_change_timer: int = 0
        self._welcome_dialog: WelcomeDialog | None = None

        self._setup_window()
        self._setup_actions()
        self._setup_content()
        self._setup_window_tracking()

        # Show welcome window on first launch (after window is mapped)
        if WelcomeDialog.should_show(application.settings_service):
            GLib.idle_add(self._show_welcome)

        logger.debug("Window initialized")

    @property
    def settings(self):
        """Current application settings."""
        return self._app.settings

    def _setup_window(self) -> None:
        """Configure window properties."""
        settings = self.settings
        self.set_default_size(
            settings.window.width,
            settings.window.height,
        )
        self.set_size_request(WINDOW_WIDTH_MIN, WINDOW_HEIGHT_MIN)

        if settings.window.maximized:
            self.maximize()

        self.set_title(APP_NAME)

    def _setup_content(self) -> None:
        """Two-pane layout: settings sidebar + content, with a dark controls bar.

        Mirrors the BigLinux Audio Converter visual identity — dual header bars,
        a `.sidebar` options pane on the left, the main text/speak area on the
        right, and a persistent dark player bar at the bottom.
        """
        self._toast_overlay = Adw.ToastOverlay()

        # MainView holds the logic + the two content boxes (sidebar/content).
        self._main_view = MainView(
            tts_service=self._app.tts_service,
            settings_service=self._app.settings_service,
            on_toast=self.show_toast,
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Sidebar + content. Below 600sp the sidebar collapses and opens over
        # the content from a header button (the window minimum is 360 px; two
        # fixed columns would not fit and the content would be cut off).
        split, self._sidebar_button = create_sidebar_split()
        self._split = split

        # ── LEFT: settings sidebar (own header with app icon + title) ──
        left = Adw.ToolbarView()
        left.add_css_class("sidebar")
        self._left_pane = left

        # Minimal sidebar header — no title/icon/window-controls (those live on
        # the right header). Kept only so the sidebar aligns with the content
        # header height. A "Settings" label gives the pane a quiet heading.
        left_header = Adw.HeaderBar()
        left_header.add_css_class("sidebar")
        left_header.set_show_start_title_buttons(False)
        left_header.set_show_end_title_buttons(False)
        # App icon at the top-left corner of the sidebar.
        app_icon = Gtk.Image.new_from_icon_name("tts-biglinux")
        app_icon.set_pixel_size(22)
        app_icon.set_margin_start(4)
        left_header.pack_start(app_icon)
        left_header.set_title_widget(Gtk.Label(label=_("Settings")))
        left.add_top_bar(left_header)

        left_scroll = Gtk.ScrolledWindow()
        left_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left_scroll.set_vexpand(True)
        left_scroll.set_child(self._main_view.sidebar_box)
        left.set_content(left_scroll)

        # ── RIGHT: main content (own header with history toggle + menu) ──
        right = Adw.ToolbarView()

        right_header = Adw.HeaderBar()
        # No start title-buttons here (that's where the decoration puts the app
        # icon/menu) — the app icon lives only in the sidebar. Keep the end
        # buttons (minimize/maximize/close).
        right_header.set_show_start_title_buttons(False)

        # History opens from the main menu; while it is shown, a Back button
        # returns to the reading area (Escape / Alt+Left do the same).
        self._back_button = Gtk.Button(icon_name="go-previous-symbolic")
        self._back_button.set_tooltip_text(_("Back"))
        self._back_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Back")])
        self._back_button.set_action_name("win.show-main")
        self._back_button.set_visible(False)
        right_header.pack_start(self._back_button)
        # Narrow windows: show/hide the settings sidebar.
        right_header.pack_start(self._sidebar_button)
        self._menu_button = self._create_menu_button()
        right_header.pack_end(self._menu_button)
        self._window_title = Adw.WindowTitle(title=APP_NAME, subtitle=_("Text narrator"))
        right_header.set_title_widget(self._window_title)
        right.add_top_bar(right_header)

        self._content_stack = Gtk.Stack()
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._content_stack.set_vexpand(True)

        self._content_stack.add_named(self._main_view.content_box, "tts")

        self._history_view = HistoryView(self.settings)
        self._history_view.on_read_again = self._read_again
        self._history_view.on_enable_history = lambda: self._main_view.set_history_enabled(True)
        self._content_stack.add_named(self._history_view, "history")
        right.set_content(self._content_stack)

        split.set_sidebar(left)
        split.set_content(right)
        root.append(split)

        # ── BOTTOM: dark controls bar (persistent player) ──
        root.append(self._build_controls_bar())

        # Thin OSD progress bar overlaid at the very top of the window, shown
        # only while an engine install runs (no text, no trough, no modal).
        overlay = Gtk.Overlay()
        overlay.set_child(root)
        self._install_pulse_id = 0
        self._install_progress = Gtk.ProgressBar()
        self._install_progress.add_css_class("osd")
        self._install_progress.set_valign(Gtk.Align.START)
        self._install_progress.set_halign(Gtk.Align.FILL)
        self._install_progress.set_show_text(False)
        self._install_progress.set_visible(False)
        overlay.add_overlay(self._install_progress)

        self._toast_overlay.set_child(overlay)
        self.set_content(self._toast_overlay)

        # Keep the bottom bar and the card in sync with TTS and shortcut state.
        self._app.tts_service.add_on_state_changed(self._on_bar_state_changed)
        self._app.add_shortcut_listener(self._main_view.on_shortcut_status)
        self._main_view.on_shortcut_status(self._app.shortcut_status)

        self._setup_breakpoints()

    def start_install_progress(self) -> None:
        """Reveal the top OSD progress bar (pulsing) during an install."""
        self._install_progress.set_show_text(False)
        self._install_progress.set_visible(True)
        if not self._install_pulse_id:
            self._install_pulse_id = GLib.timeout_add(120, self._pulse_install)

    def _pulse_install(self) -> bool:
        self._install_progress.pulse()
        return True

    def stop_install_progress(self) -> None:
        """Hide the top OSD progress bar."""
        if self._install_pulse_id:
            GLib.source_remove(self._install_pulse_id)
            self._install_pulse_id = 0
        self._install_progress.set_visible(False)

    def _idle_status_text(self) -> str:
        """Instruction shown in the status bar when idle, with the shortcut."""
        from services.shortcut_service import display_text

        status = self._app.shortcut_status
        accel = self.settings.shortcut.keybinding
        if status.accel == accel and status.registered is False:
            return _("The shortcut is not active — open Advanced options to fix it")
        return _("Select text and press {key} to read it aloud").format(
            key=display_text(accel)
        )

    def _build_controls_bar(self) -> Gtk.Box:
        """Build the dark bottom status bar (instructions + shortcut + voice)."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bar.add_css_class("dark-controls-bar")

        # Status area: usage instructions + configured shortcut (becomes the
        # live state — "Speaking…"/"Error" — during playback).
        self._state_label = Gtk.Label(label=self._idle_status_text())
        self._state_label.add_css_class("caption")
        self._state_label.set_ellipsize(Pango.EllipsizeMode.END)
        bar.append(self._state_label)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)

        self._bar_voice = Gtk.Label()
        self._bar_voice.add_css_class("caption")
        self._bar_voice.set_ellipsize(Pango.EllipsizeMode.END)
        self._bar_voice.set_max_width_chars(28)
        bar.append(self._bar_voice)

        return bar

    def refresh_status(self) -> None:
        """Refresh the status-bar instruction (e.g. after the shortcut changes)."""
        if not hasattr(self, "_state_label"):
            return
        self._on_bar_state_changed(self._app.tts_service.state)

    def show_history(self, *_args) -> None:
        """Show the History (always available, even with saving turned off)."""
        self._content_stack.set_visible_child_name("history")
        self._history_view.show()
        self._window_title.set_title(_("History"))
        self._window_title.set_subtitle("")
        self._back_button.set_visible(True)
        self._back_button.grab_focus()

    def show_main(self, *_args) -> None:
        """Back to the reading area (playback is not affected)."""
        self._content_stack.set_visible_child_name("tts")
        self._window_title.set_title(APP_NAME)
        self._window_title.set_subtitle(_("Text narrator"))
        self._back_button.set_visible(False)

    def _on_bar_state_changed(self, state: TTSState) -> None:
        """Reflect TTS state on the bottom controls bar (main thread)."""
        from config import TTSState

        tts = self._app.tts_service
        if state == TTSState.LOADING:
            text = _("Loading voice…")
        elif state == TTSState.SPEAKING:
            text = _("Speaking…")
        elif state == TTSState.ERROR:
            text = tts.last_error or _("Could not read the text")
        else:
            text = self._idle_status_text()
        self._state_label.set_label(text)
        if hasattr(self, "_main_view"):
            self._bar_voice.set_label(self._main_view.current_voice_label())

    def show_toast(self, message: str, timeout: int = 3) -> None:
        """Show an inline toast notification."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self._toast_overlay.add_toast(toast)
        logger.debug("Toast: %s", message)

    def _show_welcome(self) -> bool:
        """Present the welcome dialog (called via GLib.idle_add)."""
        dialog = WelcomeDialog(
            application=self._app,
            settings_service=self._app.settings_service,
        )
        self._welcome_dialog = dialog
        dialog.connect("closed", self._on_welcome_closed)
        dialog.present(self)
        return GLib.SOURCE_REMOVE

    def _on_welcome_closed(self, _dialog: WelcomeDialog) -> None:
        """Release the welcome dialog after it has been dismissed."""
        self._welcome_dialog = None

    def _setup_breakpoints(self) -> None:
        """Adaptive layout: collapse the sidebar on narrow windows."""
        bp_narrow = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 600sp")
        )
        bp_narrow.add_setter(self._split, "collapsed", True)
        bp_narrow.add_setter(self._sidebar_button, "visible", True)
        bp_narrow.connect("apply", self._on_narrow_apply)
        bp_narrow.connect("unapply", self._on_narrow_unapply)
        self.add_breakpoint(bp_narrow)

    def _on_narrow_apply(self, _bp: Adw.Breakpoint) -> None:
        """Narrow: content first; settings open over it from the header."""
        self._sidebar_button.set_active(False)
        self.add_css_class("narrow-layout")

    def _on_narrow_unapply(self, _bp: Adw.Breakpoint) -> None:
        """Wide: sidebar always shown next to the content."""
        self._split.set_show_sidebar(True)
        self.remove_css_class("narrow-layout")

    def update_history_tab_visibility(self, enabled: bool) -> None:
        """The "Save history" setting changed: the History page says whether
        new readings are kept (the page itself stays available)."""
        if hasattr(self, "_history_view"):
            self._history_view.set_history_enabled(enabled)

    def _read_again(self, text: str) -> None:
        """History "Read again": the text with the current voice settings."""
        app = self.get_application()
        if app is not None and text:
            app.tts_service.speak(text, **app.speak_options())

    def _build_menu_model(self) -> Gio.Menu:
        menu = Gio.Menu.new()

        # Views
        views = Gio.Menu.new()
        views.append(_("History"), "win.show-history")
        menu.append_section(None, views)

        # Preferences
        prefs = Gio.Menu.new()
        prefs.append(_("Show tray icon"), "win.toggle-tray")
        prefs.append(_("Restore Defaults…"), "win.restore-defaults")
        menu.append_section(None, prefs)

        # Help / Quit
        section = Gio.Menu.new()
        section.append(_("Welcome"), "win.show-welcome")
        section.append(_("About BigLinux TTS"), "app.about")
        section.append(_("Quit"), "app.quit")
        menu.append_section(None, section)
        return menu

    def _create_menu_button(self) -> Gtk.MenuButton:
        """Create application menu button."""
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(self._build_menu_model())
        menu_button.set_primary(True)  # F10 opens it
        menu_button.set_tooltip_text(_("Main menu"))
        menu_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Main menu")])
        return menu_button

    def _setup_actions(self) -> None:
        """Setup window-scoped actions."""
        action = Gio.SimpleAction.new("restore-defaults", None)
        action.connect("activate", self._on_restore_defaults)
        self.add_action(action)

        # Stateful: the menu shows a check mark that matches the setting.
        tray_action = Gio.SimpleAction.new_stateful(
            "toggle-tray", None,
            GLib.Variant.new_boolean(self.settings.shortcut.show_in_launcher),
        )
        tray_action.connect("activate", self._on_toggle_tray)
        self.add_action(tray_action)

        welcome_action = Gio.SimpleAction.new("show-welcome", None)
        welcome_action.connect("activate", lambda *_: self._show_welcome())
        self.add_action(welcome_action)

        history_action = Gio.SimpleAction.new("show-history", None)
        history_action.connect("activate", self.show_history)
        self.add_action(history_action)

        main_action = Gio.SimpleAction.new("show-main", None)
        main_action.connect("activate", self.show_main)
        self.add_action(main_action)

        app = self.get_application()
        if app is not None:
            app.set_accels_for_action("win.show-history", ["<Control>h"])
            app.set_accels_for_action("win.show-main", ["<Alt>Left"])

        # Escape leaves History.
        keys = Gtk.EventControllerKey()

        def _on_key(_c, keyval, _code, state) -> bool:
            from gi.repository import Gdk

            if keyval == Gdk.KEY_Escape and self._content_stack.get_visible_child_name() == "history":
                self.show_main()
                return True
            return False

        keys.connect("key-pressed", _on_key)
        self.add_controller(keys)

    def sync_tray_action(self, enabled: bool) -> None:
        action = self.lookup_action("toggle-tray")
        if action is not None:
            action.set_state(GLib.Variant.new_boolean(enabled))

    def _on_restore_defaults(
        self,
        action: Gio.SimpleAction | None = None,
        param: GLib.Variant | None = None,
    ) -> None:
        """Show confirmation dialog for restoring defaults."""
        dialog = Adw.AlertDialog.new(
            _("Restore settings?"),
            _("This will return all adjustments to their original defaults."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_restore_confirmed)
        dialog.present(self)

    def _on_restore_confirmed(self, dialog: Adw.AlertDialog, response: str) -> None:
        """Handle restore confirmation response."""
        if response == "restore" and hasattr(self, "_main_view"):
            self._main_view.restore_defaults()

    def _on_toggle_tray(
        self, action: Gio.SimpleAction, param: GLib.Variant | None
    ) -> None:
        """Toggle tray icon state from menu."""
        if hasattr(self, "_main_view"):
            current = self.settings.shortcut.show_in_launcher
            self._main_view.set_launcher_enabled(not current)

    def _setup_window_tracking(self) -> None:
        """Track window size and maximize state changes."""
        self.connect("notify::default-width", self._on_size_changed)
        self.connect("notify::default-height", self._on_size_changed)
        self.connect("notify::maximized", self._on_maximized_changed)

    def _on_size_changed(self, widget: Gtk.Widget, param: object) -> None:
        """Debounced window size save."""
        if self._size_change_timer:
            GLib.source_remove(self._size_change_timer)
        self._size_change_timer = GLib.timeout_add(500, self._save_window_state)

    def _on_maximized_changed(self, widget: Gtk.Widget, param: object) -> None:
        """Save maximized state."""
        settings = self.settings
        settings.window.maximized = self.is_maximized()
        save_settings(settings)

    def _save_window_state(self) -> bool:
        """Save current window size."""
        self._size_change_timer = 0
        if not self.is_maximized():
            width = self.get_default_size()[0]
            height = self.get_default_size()[1]
            if width > 0 and height > 0:
                settings = self.settings
                settings.window.width = width
                settings.window.height = height
                save_settings(settings)
        return GLib.SOURCE_REMOVE
