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
        self._ensure_shortcut_registered()

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

    # ── Shortcut Registration ────────────────────────────────────────

    def _ensure_shortcut_registered(self) -> None:
        """Register Alt+V shortcut with KGlobalAccel if not already registered."""
        import subprocess
        from pathlib import Path

        rc_path = Path.home() / ".config" / "kglobalshortcutsrc"
        shortcut = self.settings.shortcut.keybinding

        # Convert GTK accelerator to KDE format
        kde_shortcut = shortcut
        kde_shortcut = kde_shortcut.replace("<Control>", "Ctrl+")
        kde_shortcut = kde_shortcut.replace("<Shift>", "Shift+")
        kde_shortcut = kde_shortcut.replace("<Alt>", "Alt+")
        kde_shortcut = kde_shortcut.replace("<Super>", "Meta+")
        if "+" in kde_shortcut:
            parts = kde_shortcut.rsplit("+", 1)
            kde_shortcut = parts[0] + "+" + parts[1].upper()
        else:
            kde_shortcut = kde_shortcut.upper()

        # Check if already registered
        already_registered = False
        if rc_path.exists():
            try:
                content = rc_path.read_text(encoding="utf-8")
                if "[bigtts.desktop]" in content:
                    already_registered = True
            except OSError:
                pass

        if already_registered:
            logger.debug("Shortcut already registered in kglobalshortcutsrc")
            return

        logger.info("Registering global shortcut: %s", kde_shortcut)

        # Ensure kglobalaccel symlink exists locally
        local_kga = Path.home() / ".local" / "share" / "kglobalaccel"
        local_kga.mkdir(parents=True, exist_ok=True)
        symlink_path = local_kga / "bigtts.desktop"
        system_desktop = Path("/usr/share/applications/bigtts.desktop")
        local_desktop = Path.home() / ".local" / "share" / "applications" / "bigtts.desktop"

        target = local_desktop if local_desktop.exists() else system_desktop
        if target.exists() and not symlink_path.exists():
            try:
                symlink_path.symlink_to(target)
                logger.info("Created kglobalaccel symlink: %s -> %s", symlink_path, target)
            except OSError as e:
                logger.warning("Could not create kglobalaccel symlink: %s", e)

        # Register via kwriteconfig6
        try:
            subprocess.run(
                [
                    "kwriteconfig6",
                    "--file", "kglobalshortcutsrc",
                    "--group", "bigtts.desktop",
                    "--key", "_launch",
                    f"{kde_shortcut}\t,{kde_shortcut}\t,Speech or stop selected text",
                ],
                timeout=5,
                check=False,
            )
            logger.info("Shortcut registered in kglobalshortcutsrc")
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("Could not register shortcut: %s", e)

        # Notify KGlobalAccel to reload
        try:
            subprocess.run(
                ["dbus-send", "--type=signal", "--session",
                 "/KGlobalSettings", "org.kde.KGlobalSettings.notifyChange",
                 "int32:3", "int32:0"],
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
