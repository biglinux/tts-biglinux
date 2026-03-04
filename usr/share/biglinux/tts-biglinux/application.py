"""
Main Adw.Application class for BigLinux TTS.

Handles application lifecycle, services, and global actions.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from config import (
    APP_DEVELOPERS,
    APP_ID,
    APP_ISSUE_URL,
    APP_NAME,
    APP_VERSION,
    APP_WEBSITE,
)
from resources import load_css
from services.settings_service import SettingsService
from services.tray_service import MenuItem, TrayIcon
from services.tts_service import TTSService
from utils.i18n import _
from window import TTSWindow

if TYPE_CHECKING:
    from config import AppSettings

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

        # System tray icon
        self._tray: TrayIcon | None = None

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
        Gtk.Window.set_default_icon_name("tts-biglinux")
        self._ensure_shortcut_registered()
        self._setup_tray_icon()

    def _on_activate(self, app: Adw.Application) -> None:
        """Application activate — create or present window."""
        logger.debug("Application activated")
        if self._window is None:
            self._window = TTSWindow(application=app)
            self._window.connect("close-request", self._on_window_close_request)
        self._window.present()

    def _on_shutdown(self, app: Adw.Application) -> None:
        """Application shutdown — cleanup resources."""
        logger.debug("Application shutdown")

        if self._tray is not None:
            self._tray.unregister()

        if self._tts_service is not None:
            self._tts_service.cleanup()

        if self._settings_service is not None:
            self._settings_service.save_now()

    # ── Actions ──────────────────────────────────────────────────────

    def _setup_tray_icon(self) -> None:
        """Set up the system tray icon if enabled in settings."""
        if not self.settings.shortcut.show_in_launcher:
            return

        # Resolve icon fallback path for when theme lookup fails
        icon_fallback = ""
        for prefix in ["/usr/share", str(Path.home() / ".local/share")]:
            candidate = f"{prefix}/icons/hicolor/scalable/status/tts-biglinux-symbolic.svg"
            if Path(candidate).exists():
                icon_fallback = candidate
                break

        self._tray = TrayIcon(
            icon_name="tts-biglinux-symbolic",
            title=_(APP_NAME),
            tooltip=_("Text-to-speech assistant"),
            icon_path=icon_fallback,
        )
        self._tray.on_activate = self._on_tray_speak
        self._tray.set_menu([
            MenuItem(1, _("Settings"), self._on_tray_settings),
            MenuItem(2, "", separator=True),
            MenuItem(3, _("Quit"), self._on_tray_quit),
        ])
        self._tray.register()
        # Keep app alive when all windows are closed
        self.hold()

    def _on_window_close_request(self, window: Gtk.Window) -> bool:
        """Hide window to tray instead of quitting."""
        if self._tray is not None:
            window.set_visible(False)
            return True  # Prevent default close/destroy
        return False  # No tray — allow normal close

    def _on_tray_speak(self) -> None:
        """Speak selected text or toggle stop (left-click on tray)."""
        import threading

        from services.clipboard_service import get_selected_text
        from services.text_processor import process_text

        tts = self.tts_service
        if tts.is_speaking:
            tts.stop()
            return

        def _capture_and_speak() -> None:
            result = get_selected_text(self.settings.text.max_chars)
            logger.debug("Tray speak: clipboard result=%s", result)
            if not result.text:
                return

            processed = process_text(
                result.text,
                expand_abbreviations=self.settings.text.expand_abbreviations,
                process_special_chars=self.settings.text.process_special_chars,
                process_urls=self.settings.text.process_urls,
                strip_formatting=self.settings.text.strip_formatting,
            )
            logger.debug("Tray speak: processed text length=%d", len(processed) if processed else 0)
            if not processed:
                return

            speech = self.settings.speech
            logger.debug("Tray speak: backend=%s, voice=%s", speech.backend, speech.voice_id)
            def _do_speak() -> bool:
                tts.speak(
                    processed,
                    rate=speech.rate,
                    pitch=speech.pitch,
                    volume=speech.volume,
                    backend=speech.backend,
                    output_module=speech.output_module,
                    voice_id=speech.voice_id,
                )
                return False  # run only once

            GLib.idle_add(_do_speak)

        threading.Thread(target=_capture_and_speak, daemon=True).start()

    def _on_tray_settings(self) -> None:
        """Show settings window from tray menu."""
        if self._window is None:
            self._window = TTSWindow(application=self)
            self._window.connect("close-request", self._on_window_close_request)
        self._window.present()

    def _on_tray_quit(self) -> None:
        """Quit the application from tray menu."""
        self.release()
        self.quit()

    def enable_tray(self) -> None:
        """Enable the system tray icon (called from settings toggle)."""
        if self._tray is not None:
            return
        self._setup_tray_icon()

    def disable_tray(self) -> None:
        """Disable the system tray icon (called from settings toggle)."""
        if self._tray is None:
            return
        self._tray.unregister()
        self._tray = None
        self.release()

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
        about = Adw.AboutWindow(
            transient_for=self._window,
            application_name=_(APP_NAME),
            application_icon="tts-biglinux",
            version=APP_VERSION,
            developers=APP_DEVELOPERS,
            license_type=Gtk.License.GPL_3_0,
            website=APP_WEBSITE,
            issue_url=APP_ISSUE_URL,
        )
        about.present()

    def _on_quit(
        self, action: Gio.SimpleAction, param: GLib.Variant | None
    ) -> None:
        """Quit the application."""
        logger.info("Quit action triggered")
        if self._tray is not None:
            self.release()
        self.quit()

    # ── Shortcut Registration ────────────────────────────────────────

    def _ensure_shortcut_registered(self) -> None:
        """Register shortcut with KGlobalAccel (services group) on Plasma 6."""
        import subprocess
        from pathlib import Path

        # Disable legacy khotkeys binding (it hardcodes Alt+V and conflicts
        # with the new configurable shortcut mechanism)
        self._disable_legacy_khotkeys()

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

        # Check if already registered correctly in the services group
        already_correct = False
        if rc_path.exists():
            try:
                content = rc_path.read_text(encoding="utf-8")
                import re
                # Match within [services][biglinux-tts-speak.desktop] group
                match = re.search(
                    r"\[services\]\[biglinux-tts-speak\.desktop\]\s*\n_launch=([^\t\n]+)",
                    content,
                )
                if match and match.group(1) == kde_shortcut:
                    already_correct = True
            except OSError:
                pass

        # ── Radical Cleanup ──────────────────────────────────────────
        # Fix: Unregister via DBus to remove active zombies, not just config entries
        self._radical_dbus_cleanup()

        for group in ["khotkeys", "biglinux-tts-speak.desktop", "bigtts.desktop", "tts-speak.desktop"]:
            try:
                subprocess.run(
                    ["kwriteconfig6", "--file", "kglobalshortcutsrc", "--group", group, "--key", "_launch", "--delete"],
                    timeout=2, check=False
                )
            except: pass

        # Ensure the desktop file exists unconditionally, so KDE has a target
        # for the [services] global shortcut.
        local_apps = Path.home() / ".local" / "share" / "applications"
        desktop_dst = local_apps / "biglinux-tts-speak.desktop"
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Exec=biglinux-tts-speak\n"
            "Icon=tts-biglinux\n"
            "Categories=Utility;Accessibility;\n"
            "StartupNotify=false\n"
            "NoDisplay=true\n"
            f"X-KDE-Shortcuts={kde_shortcut}\n"
            "Name=Speech or stop selected text\n"
            "Name[pt_BR]=Narrador de texto\n"
        )
        try:
            desktop_dst.parent.mkdir(parents=True, exist_ok=True)
            desktop_dst.write_text(content)
        except OSError as e:
            logger.warning("Could not write desktop file: %s", e)

        # Update the desktop database to ensure KDE picks it up
        try:
            subprocess.run(["update-desktop-database", str(local_apps)], timeout=5, check=False)
            subprocess.run(["kbuildsycoca6", "--noincremental"], timeout=10, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass

        # Now register via kwriteconfig6 in the services group (The correct way for Plasma 6)
        try:
            subprocess.run(
                [
                    "kwriteconfig6",
                    "--file", "kglobalshortcutsrc",
                    "--group", "services",
                    "--group", "biglinux-tts-speak.desktop",
                    "--key", "_launch",
                    f"{kde_shortcut}\t{kde_shortcut}\tSpeech or stop selected text",
                ],
                timeout=5,
                check=False,
            )
            logger.info("Shortcut registered in kglobalshortcutsrc [services]")
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

    @staticmethod
    def _radical_dbus_cleanup() -> None:
        """Explicitly unregister legacy components from KGlobalAccel via DBus."""
        import subprocess
        zombies = [
            ("khotkeys", "Launch tts-biglinux"),
            ("khotkeys", "_launch"),
            ("bigtts.desktop", "_launch"),
            ("tts-speak.desktop", "_launch"),
        ]
        for comp, action in zombies:
            for dbus_cmd in [["qdbus6"], ["qdbus"], ["dbus-send", "--session", "--type=method_call", "--dest=org.kde.kglobalaccel"]]:
                try:
                    if "dbus-send" in dbus_cmd:
                        subprocess.run(
                            dbus_cmd + ["/kglobalaccel", "org.kde.KGlobalAccel.unregister", comp, action],
                            timeout=1, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL
                        )
                    else:
                        subprocess.run(
                            dbus_cmd + ["org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel.unregister", comp, action],
                            timeout=1, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL
                        )
                except: pass

    @staticmethod
    def _disable_legacy_khotkeys() -> None:
        """Disable legacy khotkeys binding if still active.

        On Plasma 6, the khotkeys module is typically not loaded. This method
        checks if it is and, if so, asks kded to unload it to prevent the
        hardcoded Alt+V from /usr/share/khotkeys/ttsbiglinux.khotkeys from
        interfering with the configurable shortcut.
        """
        import subprocess

        # Check if khotkeys module is loaded in kded6
        try:
            result = subprocess.run(
                [
                    "qdbus6", "org.kde.kded6", "/kded",
                    "org.kde.kded6.loadedModules",
                ],
                capture_output=True, text=True, timeout=3,
            )
            if "khotkeys" not in result.stdout:
                return  # module not loaded, nothing to do
        except (OSError, subprocess.TimeoutExpired):
            return

        # khotkeys is loaded — try to tell it to reload so it picks up
        # the disabled version of ttsbiglinux.khotkeys
        logger.info("khotkeys module is loaded, requesting reload")
        try:
            subprocess.run(
                [
                    "dbus-send", "--session", "--type=method_call",
                    "--dest=org.kde.kded6",
                    "/modules/khotkeys",
                    "org.kde.khotkeys.reread_configuration",
                ],
                timeout=3, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
