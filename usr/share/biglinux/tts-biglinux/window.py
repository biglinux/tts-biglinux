"""
Main application window for BigLinux TTS.

Implements Adw.ApplicationWindow with header, navigation, and main view.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from config import (
    APP_NAME,
    WINDOW_HEIGHT_MIN,
    WINDOW_WIDTH_MIN,
    save_settings,
)
from ui.history_view import HistoryView
from ui.main_view import MainView
from ui.welcome_dialog import WelcomeWindow
from utils.i18n import _

if TYPE_CHECKING:
    from application import TTSApplication
    from config import TTSState

logger = logging.getLogger(__name__)


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

        self._setup_window()
        self._setup_actions()
        self._setup_content()
        self._setup_window_tracking()

        # Show welcome window on first launch (after window is mapped)
        if WelcomeWindow.should_show(application.settings_service):
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

        self.set_title(_(APP_NAME))

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

        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        split.set_position(320)
        split.set_vexpand(True)
        split.set_shrink_start_child(False)
        split.set_shrink_end_child(False)
        split.set_resize_start_child(False)
        self._split = split

        # ── LEFT: settings sidebar (own header with app icon + title) ──
        left = Adw.ToolbarView()
        left.add_css_class("sidebar")
        left.set_size_request(280, -1)
        self._left_pane = left

        left_header = Adw.HeaderBar()
        left_header.add_css_class("sidebar")
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        app_icon = Gtk.Image.new_from_icon_name("tts-biglinux")
        app_icon.set_pixel_size(20)
        title_box.append(app_icon)
        title_lbl = Gtk.Label(label=_(APP_NAME))
        title_lbl.add_css_class("heading")
        title_box.append(title_lbl)
        left_header.set_title_widget(title_box)
        left.add_top_bar(left_header)

        left_scroll = Gtk.ScrolledWindow()
        left_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left_scroll.set_vexpand(True)
        left_scroll.set_child(self._main_view.sidebar_box)
        left.set_content(left_scroll)

        # ── RIGHT: main content (own header with history toggle + menu) ──
        right = Adw.ToolbarView()
        right.set_size_request(340, -1)

        right_header = Adw.HeaderBar()
        # Sidebar reveal toggle — only shown when the window is narrow.
        self._sidebar_toggle = Gtk.ToggleButton()
        self._sidebar_toggle.set_icon_name("sidebar-show-symbolic")
        self._sidebar_toggle.set_tooltip_text(_("Settings"))
        self._sidebar_toggle.add_css_class("flat")
        self._sidebar_toggle.set_visible(False)
        self._sidebar_toggle.connect(
            "toggled", lambda b: self._left_pane.set_visible(b.get_active())
        )
        right_header.pack_start(self._sidebar_toggle)

        self._history_toggle = Gtk.ToggleButton()
        self._history_toggle.set_icon_name("document-open-recent-symbolic")
        self._history_toggle.set_tooltip_text(_("History"))
        self._history_toggle.add_css_class("flat")
        self._history_toggle.connect("toggled", self._on_history_toggled)
        right_header.pack_start(self._history_toggle)
        right_header.pack_end(self._create_menu_button())
        right_header.set_title_widget(
            Adw.WindowTitle(title=_(APP_NAME), subtitle=_("Text narrator"))
        )
        right.add_top_bar(right_header)

        self._content_stack = Gtk.Stack()
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._content_stack.set_vexpand(True)

        self._content_stack.add_named(self._main_view.content_box, "tts")

        self._history_view = HistoryView()
        self._content_stack.add_named(self._history_view, "history")
        right.set_content(self._content_stack)

        split.set_start_child(left)
        split.set_end_child(right)
        root.append(split)

        # ── BOTTOM: dark controls bar (persistent player) ──
        root.append(self._build_controls_bar())

        self._toast_overlay.set_child(root)
        self.set_content(self._toast_overlay)

        # History toggle only when history is enabled.
        self._history_toggle.set_visible(self.settings.history.enabled)

        # Keep the bottom bar in sync with TTS state.
        self._app.tts_service.add_on_state_changed(self._on_bar_state_changed)

        self._setup_breakpoints()

    def _build_controls_bar(self) -> Gtk.Box:
        """Build the dark bottom controls bar (Speak/Stop + state + voice)."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bar.add_css_class("dark-controls-bar")

        self._speak_btn = Gtk.Button()
        self._speak_content = Adw.ButtonContent(
            icon_name="media-playback-start-symbolic", label=_("Speak")
        )
        self._speak_btn.set_child(self._speak_content)
        self._speak_btn.add_css_class("suggested-action")
        self._speak_btn.add_css_class("pill")
        self._speak_btn.set_tooltip_text(_("Speak the text (Alt+V)"))
        self._speak_btn.connect("clicked", lambda *_: self._main_view.trigger_speak())
        bar.append(self._speak_btn)

        self._state_label = Gtk.Label(label=_("Ready"))
        self._state_label.add_css_class("caption")
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

    def _on_history_toggled(self, btn: Gtk.ToggleButton) -> None:
        """Switch the content pane between the TTS area and History."""
        if btn.get_active():
            self._history_view.reload()
            self._content_stack.set_visible_child_name("history")
        else:
            self._content_stack.set_visible_child_name("tts")

    def _on_bar_state_changed(self, state: TTSState) -> None:
        """Reflect TTS state on the bottom controls bar."""
        from config import TTSState

        def _update() -> bool:
            if state == TTSState.SPEAKING:
                self._state_label.set_label(_("Speaking…"))
                self._speak_content.set_icon_name("media-playback-stop-symbolic")
                self._speak_content.set_label(_("Stop"))
                self._speak_btn.remove_css_class("suggested-action")
                self._speak_btn.add_css_class("destructive-action")
            else:
                self._state_label.set_label(
                    _("Error") if state == TTSState.ERROR else _("Ready")
                )
                self._speak_content.set_icon_name("media-playback-start-symbolic")
                self._speak_content.set_label(_("Speak"))
                self._speak_btn.remove_css_class("destructive-action")
                self._speak_btn.add_css_class("suggested-action")
            if hasattr(self, "_main_view"):
                self._bar_voice.set_label(self._main_view.current_voice_label())
            return False

        GLib.idle_add(_update)

    def show_toast(self, message: str, timeout: int = 3) -> None:
        """Show an inline toast notification."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self._toast_overlay.add_toast(toast)
        logger.debug("Toast: %s", message)

    def _show_welcome(self) -> bool:
        """Present the welcome window (called via GLib.idle_add)."""
        win = WelcomeWindow(
            application=self._app,
            settings_service=self._app.settings_service,
        )
        win.set_transient_for(self)
        win.set_modal(True)
        win.present()
        return GLib.SOURCE_REMOVE

    def _setup_breakpoints(self) -> None:
        """Adaptive layout: collapse the sidebar on narrow windows."""
        bp_narrow = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 600sp")
        )
        bp_narrow.connect("apply", self._on_narrow_apply)
        bp_narrow.connect("unapply", self._on_narrow_unapply)
        self.add_breakpoint(bp_narrow)

    def _on_narrow_apply(self, _bp: Adw.Breakpoint) -> None:
        """Narrow: hide the sidebar; reveal it via the header toggle."""
        if hasattr(self, "_left_pane"):
            self._left_pane.set_visible(False)
        if hasattr(self, "_sidebar_toggle"):
            self._sidebar_toggle.set_visible(True)
            self._sidebar_toggle.set_active(False)
        self.add_css_class("narrow-layout")

    def _on_narrow_unapply(self, _bp: Adw.Breakpoint) -> None:
        """Wide: always show the sidebar; hide the reveal toggle."""
        if hasattr(self, "_left_pane"):
            self._left_pane.set_visible(True)
        if hasattr(self, "_sidebar_toggle"):
            self._sidebar_toggle.set_visible(False)
        if hasattr(self, "_split"):
            self._split.set_position(320)
        self.remove_css_class("narrow-layout")

    def update_history_tab_visibility(self, enabled: bool) -> None:
        """Show or hide the History toggle button."""
        if hasattr(self, "_history_toggle"):
            self._history_toggle.set_visible(enabled)
            if not enabled:
                self._history_toggle.set_active(False)
                if hasattr(self, "_content_stack"):
                    self._content_stack.set_visible_child_name("tts")

    def _create_menu_button(self) -> Gtk.MenuButton:
        """Create application menu button."""
        menu = Gio.Menu.new()

        # Tray Icon toggle
        menu.append(_("Tray icon"), "win.toggle-tray")

        # Restore defaults
        menu.append(_("Restore Defaults"), "win.restore-defaults")

        # About / Quit section
        section = Gio.Menu.new()
        section.append(_("Welcome"), "win.show-welcome")
        section.append(_("About"), "app.about")
        section.append(_("Quit"), "app.quit")
        menu.append_section(None, section)

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(menu)
        menu_button.set_tooltip_text(_("Main menu"))
        menu_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Main menu")])

        return menu_button

    def _setup_actions(self) -> None:
        """Setup window-scoped actions."""
        action = Gio.SimpleAction.new("restore-defaults", None)
        action.connect("activate", self._on_restore_defaults)
        self.add_action(action)

        tray_action = Gio.SimpleAction.new("toggle-tray", None)
        tray_action.connect("activate", self._on_toggle_tray)
        self.add_action(tray_action)

        welcome_action = Gio.SimpleAction.new("show-welcome", None)
        welcome_action.connect("activate", lambda *_: self._show_welcome())
        self.add_action(welcome_action)

    def _on_restore_defaults(
        self,
        action: Gio.SimpleAction | None = None,
        param: GLib.Variant | None = None,
    ) -> None:
        """Show confirmation dialog for restoring defaults."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Restore settings?"),
            body=_("This will return all adjustments to their original defaults."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_restore_confirmed)
        dialog.present()

    def _on_restore_confirmed(self, dialog: Adw.MessageDialog, response: str) -> None:
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
