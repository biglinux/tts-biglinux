"""
Main Adw.Application class for BigLinux TTS.

Handles application lifecycle, services, and global actions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from biglinux_tts.config import (
    APP_DEVELOPER,
    APP_ID,
    APP_ISSUE_URL,
    APP_NAME,
    APP_VERSION,
    APP_WEBSITE,
)
from biglinux_tts.resources import load_css
from biglinux_tts.services.settings_service import SettingsService
from biglinux_tts.services.tts_service import TTSService
from biglinux_tts.utils.i18n import _
from biglinux_tts.window import TTSWindow

if TYPE_CHECKING:
    from biglinux_tts.config import AppSettings

logger = logging.getLogger(__name__)


class TTSApplication(Adw.Application):
    """
    Main application class for BigLinux TTS.

    Manages lifecycle, services, and global state.
    """

    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

        # Services (lazy)
        self._tts_service: TTSService | None = None
        self._settings_service: SettingsService | None = None

        # Window
        self._window: TTSWindow | None = None

        # Signals
        self.connect("activate", self._on_activate)
        self.connect("startup", self._on_startup)
        self.connect("shutdown", self._on_shutdown)

        logger.debug("Application initialized")

    # ── Service Properties ───────────────────────────────────────────

    @property
    def tts_service(self) -> TTSService:
        """Get TTS service (lazy init)."""
        if self._tts_service is None:
            self._tts_service = TTSService()
        return self._tts_service

    @property
    def settings_service(self) -> SettingsService:
        """Get settings service (lazy init)."""
        if self._settings_service is None:
            self._settings_service = SettingsService()
        return self._settings_service

    @property
    def settings(self) -> AppSettings:
        """Current application settings."""
        return self.settings_service.get()

    # ── Lifecycle ────────────────────────────────────────────────────

    def _on_startup(self, app: Adw.Application) -> None:
        """Application startup — load CSS and create actions."""
        logger.debug("Application startup")
        load_css()
        self._create_actions()
        GLib.set_application_name(_(APP_NAME))
        Gtk.Window.set_default_icon_name("biglinux-tts")

    def _on_activate(self, app: Adw.Application) -> None:
        """Application activate — create or present window."""
        logger.debug("Application activated")
        if self._window is None:
            self._window = TTSWindow(application=app)
        self._window.present()

    def _on_shutdown(self, app: Adw.Application) -> None:
        """Application shutdown — cleanup resources."""
        logger.debug("Application shutdown")

        if self._tts_service is not None:
            self._tts_service.cleanup()

        if self._settings_service is not None:
            self._settings_service.save_now()

    # ── Actions ──────────────────────────────────────────────────────

    def _create_actions(self) -> None:
        """Create application-level actions."""
        # About
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

        # Quit
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit)
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def _on_about(
        self, action: Gio.SimpleAction, param: GLib.Variant | None
    ) -> None:
        """Show about dialog."""
        about = Adw.AboutDialog.new()
        about.set_application_name(_(APP_NAME))
        about.set_version(APP_VERSION)
        about.set_developer_name(APP_DEVELOPER)
        about.set_license_type(Gtk.License.GPL_3_0)
        about.set_website(APP_WEBSITE)
        about.set_issue_url(APP_ISSUE_URL)
        about.set_application_icon("biglinux-tts")

        about.add_credit_section(
            _("Technologies used"),
            [
                "speech-dispatcher",
                "RHVoice",
                "espeak-ng",
                "Piper Neural TTS",
                "GTK4 / Libadwaita",
            ],
        )

        about.add_credit_section(
            _("License"),
            [
                _(
                    "This interface is licensed under GPL-3.0. "
                    "TTS engines have their own licenses."
                )
            ],
        )

        about.present(self._window)

    def _on_quit(
        self, action: Gio.SimpleAction, param: GLib.Variant | None
    ) -> None:
        """Quit the application."""
        logger.info("Quit action triggered")
        self.quit()
