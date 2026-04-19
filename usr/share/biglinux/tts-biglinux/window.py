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

from gi.repository import Adw, Gio, GLib, Gtk

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
        """Create main window content with tabbed layout."""
        # Toast overlay for inline notifications
        self._toast_overlay = Adw.ToastOverlay()

        # Toolbar view for header integration
        toolbar_view = Adw.ToolbarView()

        # Header bar with view switcher
        header = self._create_header_bar()
        toolbar_view.add_top_bar(header)

        # View stack for tabs
        self._view_stack = Adw.ViewStack()

        # Tab 1 — TTS settings
        self._main_view = MainView(
            tts_service=self._app.tts_service,
            settings_service=self._app.settings_service,
            on_toast=self.show_toast,
        )
        self._view_stack.add_titled_with_icon(
            self._main_view, "tts", _("TTS"),
            "audio-speakers-symbolic",
        )

        # Tab 2 — History
        self._history_view = HistoryView()
        self._history_page = self._view_stack.add_titled_with_icon(
            self._history_view, "history", _("History"),
            "document-open-recent-symbolic",
        )
        # Show/hide history tab based on settings
        history_enabled = self.settings.history.enabled
        self._history_page.set_visible(history_enabled)

        # Reload history when switching to the tab
        self._view_stack.connect(
            "notify::visible-child-name", self._on_tab_changed
        )

        toolbar_view.set_content(self._view_stack)

        # Bottom view switcher bar (shown on narrow windows)
        self._switcher_bar = Adw.ViewSwitcherBar()
        self._switcher_bar.set_stack(self._view_stack)
        self._switcher_bar.set_reveal(False)
        toolbar_view.add_bottom_bar(self._switcher_bar)

        self._toast_overlay.set_child(toolbar_view)
        self.set_content(self._toast_overlay)

        # Responsive breakpoints
        self._setup_breakpoints()

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

    def _create_header_bar(self) -> Adw.HeaderBar:
        """Create header bar with view switcher and menu."""
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)

        # View switcher as title widget
        self._view_switcher = Adw.ViewSwitcher()
        self._view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        GLib.idle_add(self._connect_switcher)
        header.set_title_widget(self._view_switcher)

        # Menu button
        menu_button = self._create_menu_button()
        header.pack_end(menu_button)

        return header

    def _connect_switcher(self) -> bool:
        """Connect view switcher to stack (called via idle_add)."""
        if hasattr(self, "_view_stack"):
            self._view_switcher.set_stack(self._view_stack)
        return GLib.SOURCE_REMOVE

    def _setup_breakpoints(self) -> None:
        """Configure adaptive breakpoints for narrow/wide layouts."""
        # < 550sp: move tab switching to bottom bar
        bp_narrow = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 550sp")
        )
        bp_narrow.add_setter(self._switcher_bar, "reveal", True)
        bp_narrow.add_setter(self._view_switcher, "visible", False)
        bp_narrow.connect("apply", self._on_narrow_apply)
        bp_narrow.connect("unapply", self._on_narrow_unapply)
        self.add_breakpoint(bp_narrow)

    def _on_narrow_apply(self, _bp: Adw.Breakpoint) -> None:
        """Apply narrow layout CSS class."""
        self._main_view.add_css_class("narrow-layout")

    def _on_narrow_unapply(self, _bp: Adw.Breakpoint) -> None:
        """Remove narrow layout CSS class."""
        self._main_view.remove_css_class("narrow-layout")

    def _on_tab_changed(
        self, stack: Adw.ViewStack, _param: object
    ) -> None:
        """Handle tab switch — reload history when visiting the tab."""
        if stack.get_visible_child_name() == "history":
            self._history_view.reload()

    def update_history_tab_visibility(self, enabled: bool) -> None:
        """Show or hide the History tab."""
        if hasattr(self, "_history_page"):
            self._history_page.set_visible(enabled)
            if not enabled and hasattr(self, "_view_stack"):
                self._view_stack.set_visible_child_name("tts")

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
