"""
Main Adw.Application class for BigLinux TTS.

Handles application lifecycle, services, and global actions.
"""

from __future__ import annotations

import logging
import subprocess
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
from services.desktop_integration_service import DesktopIntegrationService
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
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )

        # Register --speak option for GApplication command line handling
        self.add_main_option(
            "speak", 0,
            GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Speak selected text", None,
        )

        # Services (lazy)
        self._tts_service: TTSService | None = None
        self._settings_service: SettingsService | None = None

        # System tray icon
        self._tray: TrayIcon | None = None

        # Window
        self._window: TTSWindow | None = None

        # Speech queue for "queue" playback mode
        self._speech_queue: list[dict] = []

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
            self._tts_service = TTSService(settings=self.settings)
            self._tts_service.add_on_state_changed(self._on_tts_state_changed)
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

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        """Handle command line — supports --speak for remote activation."""
        options = command_line.get_options_dict()
        if options.contains("speak"):
            logger.debug("--speak flag received, triggering tray speak")
            self._on_tray_speak()
        else:
            self.activate()
        return 0

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
        # Resolve tray icons: dark-mode, light-mode, symbolic fallback
        icon_dark = ""
        icon_light = ""
        icon_fallback = ""
        # Check installed + local share, then dev repo
        repo_status = Path(__file__).resolve().parent.parent.parent / "icons" / "hicolor" / "scalable" / "status"
        search_dirs = [
            "/usr/share/icons/hicolor/scalable/status",
            str(Path.home() / ".local/share/icons/hicolor/scalable/status"),
            str(repo_status),
        ]
        for d in search_dirs:
            dp = Path(d)
            if not dp.is_dir():
                continue
            if not icon_dark and (dp / "tts-biglinux-dark.svg").exists():
                icon_dark = str(dp / "tts-biglinux-dark.svg")
            if not icon_light and (dp / "tts-biglinux-light.svg").exists():
                icon_light = str(dp / "tts-biglinux-light.svg")
            if not icon_fallback and (dp / "tts-biglinux-symbolic.svg").exists():
                icon_fallback = str(dp / "tts-biglinux-symbolic.svg")

        self._tray = TrayIcon(
            title=_(APP_NAME),
            tooltip=_("Text-to-speech assistant"),
            icon_dark_path=icon_dark,
            icon_light_path=icon_light,
            icon_path=icon_fallback,
        )
        self._tray.on_activate = self._on_tray_speak
        self._tray.set_menu([
            MenuItem(1, _("Read text"), self._on_tray_speak),
            MenuItem(2, _("Settings"), self._on_tray_settings),
            MenuItem(3, "", separator=True),
            MenuItem(4, _("Quit"), self._on_tray_quit),
        ])
        self._tray.register()
        # Keep app alive when all windows are closed
        self.hold()

    _notif_dismiss_id: int = 0
    _notif_id: int = 0  # D-Bus notification ID
    _notif_text_len: int = 0  # Length of notified text for proportional delay
    _notif_body: str = ""  # Last notification body for re-use on countdown

    def _on_tts_state_changed(self, state: "TTSState") -> None:
        """Notify tray icon of TTS state changes and process speech queue."""
        from config import TTSState
        if self._tray is None:
            return
        speaking = state == TTSState.SPEAKING
        self._tray.set_speaking(speaking, _("Playing…") if speaking else "")

        if speaking:
            # Cancel pending dismiss
            if self._notif_dismiss_id:
                GLib.source_remove(self._notif_dismiss_id)
                self._notif_dismiss_id = 0

            # Get text being spoken
            spoken_text = ""
            if self._tts_service:
                spoken_text = getattr(self._tts_service, "_last_spoken_text", "")
            display_text = spoken_text or _("Playing…")
            if len(display_text) > 120:
                display_text = display_text[:120] + "…"

            self._notif_text_len = len(spoken_text) if spoken_text else 20
            self._notif_body = display_text
            # Show persistent notification (no auto-expire during speech)
            self._show_dbus_notification(display_text, expire_timeout=0)
        else:
            # Process speech queue (queue mode)
            if self._speech_queue:
                params = self._speech_queue.pop(0)
                GLib.idle_add(lambda p=params: self.tts_service.speak(**p) and False)
                return

            # Proportional delay: 40ms per char, min 2s
            delay_ms = max(2000, self._notif_text_len * 40)

            # Re-send same text with expire_timeout → KDE shows countdown bar
            self._show_dbus_notification(
                self._notif_body or _("Playing…"),
                expire_timeout=delay_ms,
            )

            # Fallback: manual dismiss if notification server ignores expire_timeout
            if self._notif_dismiss_id:
                GLib.source_remove(self._notif_dismiss_id)
            self._notif_dismiss_id = GLib.timeout_add(
                delay_ms, self._dismiss_notification,
            )

    def _show_dbus_notification(
        self, body: str, *, expire_timeout: int = 0,
    ) -> None:
        """Show notification via org.freedesktop.Notifications D-Bus.

        Args:
            body: Notification body text.
            expire_timeout: Auto-dismiss delay in ms (0 = server decides).
                KDE shows a countdown bar when > 0.
        """
        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", None,
            )
            result = proxy.call_sync(
                "Notify",
                GLib.Variant("(susssasa{sv}i)", (
                    APP_NAME,           # app_name
                    self._notif_id,     # replaces_id (0 = new)
                    "tts-biglinux",     # icon
                    APP_NAME,           # summary
                    body,               # body
                    [],                 # actions
                    {},                 # hints
                    expire_timeout,     # expire_timeout (ms, 0 = server decides)
                )),
                Gio.DBusCallFlags.NONE, -1, None,
            )
            self._notif_id = result.unpack()[0]
        except Exception:
            pass

    def _dismiss_notification(self) -> bool:
        """Close notification via D-Bus after debounce period."""
        self._notif_dismiss_id = 0
        if self._notif_id:
            try:
                proxy = Gio.DBusProxy.new_for_bus_sync(
                    Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                    "org.freedesktop.Notifications",
                    "/org/freedesktop/Notifications",
                    "org.freedesktop.Notifications", None,
                )
                proxy.call_sync(
                    "CloseNotification",
                    GLib.Variant("(u)", (self._notif_id,)),
                    Gio.DBusCallFlags.NONE, -1, None,
                )
            except Exception:
                pass
            self._notif_id = 0
        return False

    def _on_window_close_request(self, window: Gtk.Window) -> bool:
        """Hide window to tray instead of quitting."""
        if self._tray is not None:
            window.set_visible(False)
            return True  # Prevent default close/destroy
        return False  # No tray — allow normal close

    def _on_tray_speak(self) -> None:
        """Speak selected text (left-click on tray).

        Respects playback_mode setting:
        - interrupt: stop previous speech, start new
        - queue: enqueue if speaking, auto-play when done
        - simultaneous: play new speech without stopping
        """
        import threading

        from services.clipboard_service import get_selected_text

        tts = self.tts_service
        mode = self.settings.history.playback_mode  # interrupt|queue|simultaneous

        def _capture_and_speak() -> None:
            result = get_selected_text(self.settings.text.max_chars)
            logger.debug("Tray speak: clipboard result=%s", result)
            if not result.text:
                # No new text — stop and clear queue
                self._speech_queue.clear()
                if tts.is_speaking:
                    GLib.idle_add(tts.stop)
                return

            raw_text = result.text
            logger.debug("Tray speak: raw text length=%d, mode=%s", len(raw_text), mode)

            speech = self.settings.speech
            text_cfg = self.settings.text

            speak_params = dict(
                text=raw_text,
                rate=speech.rate,
                pitch=speech.pitch,
                volume=speech.volume,
                backend=speech.backend,
                output_module=speech.output_module,
                voice_id=speech.voice_id,
                expand_abbreviations=text_cfg.expand_abbreviations,
                process_special_chars=text_cfg.process_special_chars,
                process_urls=text_cfg.process_urls,
                strip_formatting=text_cfg.strip_formatting,
            )

            def _do_speak() -> bool:
                if mode == "queue" and tts.is_speaking:
                    # Enqueue — will auto-play when current finishes
                    self._speech_queue.append(speak_params)
                    logger.debug("Speech queued (%d pending)", len(self._speech_queue))
                elif mode == "simultaneous":
                    # Play without stopping previous
                    tts.speak(**speak_params, stop_previous=False)
                else:
                    # Interrupt (default): stop + start
                    tts.speak(**speak_params)
                return False

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
        """Register global shortcut for the current desktop environment.

        Supports KDE/Plasma, GNOME, XFCE, and Cinnamon.
        """
        shortcut = self.settings.shortcut.keybinding
        de = DesktopIntegrationService.detect_desktop_environment()

        if de == "kde":
            self._ensure_shortcut_registered_kde(shortcut)
        else:
            # GNOME, XFCE, Cinnamon, or unknown — use cross-DE dispatcher
            DesktopIntegrationService.register_shortcut_for_current_de(shortcut)

    def _ensure_shortcut_registered_kde(self, shortcut: str) -> None:
        """Register shortcut with KGlobalAccel (services group) on Plasma 6."""
        # Disable legacy khotkeys binding (it hardcodes Alt+V and conflicts
        # with the new configurable shortcut mechanism)
        self._disable_legacy_khotkeys()

        rc_path = Path.home() / ".config" / "kglobalshortcutsrc"

        # Convert GTK accelerator to KDE format
        kde_shortcut = DesktopIntegrationService.gtk_accel_to_kde(shortcut)

        # Check if already registered correctly in the services group
        already_correct = False
        if rc_path.exists():
            try:
                content = rc_path.read_text(encoding="utf-8")
                import re
                match = re.search(
                    r"\[services\]\[biglinux-tts-speak\.desktop\]\s*\n_launch=([^\t\n]+)",
                    content,
                )
                if match and match.group(1) == kde_shortcut:
                    already_correct = True
            except OSError:
                pass

        if already_correct:
            logger.debug("Shortcut already registered correctly: %s", kde_shortcut)
            return

        logger.info("Registering KDE global shortcut: %s", kde_shortcut)

        # Remove stale component-level entry if present (legacy, wrong group)
        try:
            subprocess.run(
                [
                    "kwriteconfig6",
                    "--file", "kglobalshortcutsrc",
                    "--group", "biglinux-tts-speak.desktop",
                    "--key", "_launch",
                    "--delete",
                ],
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

        # Register via kwriteconfig6 in the services group (Plasma 6)
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
