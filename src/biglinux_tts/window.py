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

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from biglinux_tts.config import (
    APP_NAME,
    ICON_APP,
    WINDOW_HEIGHT_MIN,
    WINDOW_WIDTH_MIN,
    save_settings,
)
from biglinux_tts.ui.main_view import MainView
from biglinux_tts.utils.i18n import _

if TYPE_CHECKING:
    from biglinux_tts.application import TTSApplication

logger = logging.getLogger(__name__)


class TTSWindow(Adw.ApplicationWindow):
    """
    Main window with header bar, navigation, and settings view.

    Follows GNOME HIG and Adwaita patterns.
    """

    def __init__(self, application: TTSApplication) -> None:
        super().__init__(application=application)

        self._app = application
        self._settings = application.settings

        # Window state tracking
        self._size_change_timer: int = 0

        self._setup_window()
        self._setup_actions()
        self._setup_content()
        self._setup_window_tracking()

        logger.debug("Window initialized")

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.set_default_size(
            self._settings.window.width,
            self._settings.window.height,
        )
        self.set_size_request(WINDOW_WIDTH_MIN, WINDOW_HEIGHT_MIN)

        if self._settings.window.maximized:
            self.maximize()

        self.set_title(_(APP_NAME))

    def _setup_content(self) -> None:
        """Create main window content."""
        # Toast overlay for inline notifications
        self._toast_overlay = Adw.ToastOverlay()

        # Toolbar view for header integration
        toolbar_view = Adw.ToolbarView()

        # Header bar
        header = self._create_header_bar()
        toolbar_view.add_top_bar(header)

        # Navigation view
        self._navigation_view = Adw.NavigationView()

        # Main settings view
        self._main_view = MainView(
            tts_service=self._app.tts_service,
            settings_service=self._app.settings_service,
            on_toast=self.show_toast,
        )
        self._navigation_view.add(self._main_view)

        toolbar_view.set_content(self._navigation_view)
        self._toast_overlay.set_child(toolbar_view)
        self.set_content(self._toast_overlay)

    def show_toast(self, message: str, timeout: int = 3) -> None:
        """Show an inline toast notification."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self._toast_overlay.add_toast(toast)
        logger.debug("Toast: %s", message)

    def _create_header_bar(self) -> Adw.HeaderBar:
        """Create header bar with icon and menu."""
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)

        # App icon in header
        if ICON_APP.exists():
            try:
                file = Gio.File.new_for_path(str(ICON_APP))
                texture = Gdk.Texture.new_from_file(file)
                icon_image = Gtk.Image.new_from_paintable(texture)
                icon_image.set_pixel_size(24)
                icon_image.set_margin_start(8)
                icon_image.set_margin_end(4)
                header.pack_start(icon_image)
            except Exception:
                logger.debug("Could not load header icon")

        # Title
        title = Adw.WindowTitle.new(_(APP_NAME), "")
        header.set_title_widget(title)

        # Menu button
        menu_button = self._create_menu_button()
        header.pack_end(menu_button)

        return header

    def _create_menu_button(self) -> Gtk.MenuButton:
        """Create application menu button."""
        menu = Gio.Menu.new()

        # Restore defaults
        menu.append(_("Restore Defaults"), "win.restore-defaults")

        # About / Quit section
        section = Gio.Menu.new()
        section.append(_("About"), "app.about")
        section.append(_("Quit"), "app.quit")
        menu.append_section(None, section)

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(menu)
        menu_button.set_tooltip_text(_("Main menu"))
        menu_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Main menu")]
        )

        return menu_button

    def _setup_actions(self) -> None:
        """Setup window-scoped actions."""
        action = Gio.SimpleAction.new("restore-defaults", None)
        action.connect("activate", self._on_restore_defaults)
        self.add_action(action)

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
        self._settings.window.maximized = self.is_maximized()
        save_settings(self._settings)

    def _save_window_state(self) -> bool:
        """Save current window size."""
        self._size_change_timer = 0
        if not self.is_maximized():
            width = self.get_default_size()[0]
            height = self.get_default_size()[1]
            if width > 0 and height > 0:
                self._settings.window.width = width
                self._settings.window.height = height
                save_settings(self._settings)
        return GLib.SOURCE_REMOVE
