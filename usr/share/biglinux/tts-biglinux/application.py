"""
Main Adw.Application class for BigLinux TTS.

Handles application lifecycle, services, and global actions.
"""

# ruff: noqa: E402  # gi.require_version must run before repository imports.

from __future__ import annotations

import logging
import signal
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from config import (
    APP_DEVELOPERS,
    APP_COPYRIGHT,
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
    from config import AppSettings, TTSState

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
            "speak",
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            _("Read the selected text aloud"),
            None,
        )

        # Services (lazy)
        self._tts_service: TTSService | None = None
        self._settings_service: SettingsService | None = None

        # System tray icon
        self._tray: TrayIcon | None = None

        # MPRIS media player (system mini-player while reading)
        self._mpris = None

        # Window
        self._window: TTSWindow | None = None

        # Speech queue for "queue" playback mode
        self._speech_queue: list[dict] = []

        # Global shortcut as confirmed by the desktop (see shortcut_service).
        from services.shortcut_service import ShortcutStatus

        self.shortcut_status = ShortcutStatus()
        self._shortcut_listeners: list = []
        self._shortcut_request = 0  # newest registration wins

        # A cold `--speak` holds the app until that request ends (no window
        # and no tray would otherwise let GApplication quit before speaking).
        self._speak_hold = False

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
        # Logout / kill: quit cleanly so no player keeps speaking orphaned.
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self._on_signal)
        logger.debug("Application startup")
        load_css()
        self._create_actions()
        GLib.set_application_name(APP_NAME)
        Gtk.Window.set_default_icon_name("tts-biglinux")
        self._setup_tray_icon()
        # Registration talks to the desktop over D-Bus/CLI tools: never on the
        # main thread, never delaying the first Alt+V.
        self.apply_shortcut(self.settings.shortcut.keybinding)
        if self.settings.show_media_player:
            self.enable_media_player()
        # Prewarm the selected neural model during idle (no audio) so the first
        # Alt+V is warm. Delayed so it never competes with UI startup.
        GLib.timeout_add_seconds(1, self._idle_prewarm)

    def _idle_prewarm(self) -> bool:
        """Idle callback: preload the selected Piper model (no audio)."""
        try:
            speech = self.settings.speech
            self.tts_service.prewarm(speech.backend, speech.voice_id)
        except Exception as e:
            logger.debug("Idle prewarm skipped: %s", e)
        return False  # one-shot

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
            if not self._speak_hold:
                self.hold()
                self._speak_hold = True
            self._on_tray_speak()
        else:
            self.activate()
        return 0

    def _on_signal(self) -> bool:
        logger.info("Termination signal: quitting")
        self.quit()
        return GLib.SOURCE_REMOVE

    def _on_shutdown(self, app: Adw.Application) -> None:
        """Application shutdown — cleanup resources."""
        logger.debug("Application shutdown")

        if self._tray is not None:
            self._tray.unregister()

        if self._mpris is not None:
            self._mpris.disable()

        if self._tts_service is not None:
            self._tts_service.cleanup()

        if self._settings_service is not None:
            self._settings_service.save_now()

    def enable_media_player(self) -> None:
        """Register the MPRIS media player (system mini-player)."""
        if self._mpris is None:
            from services.mpris_service import MprisService

            self._mpris = MprisService(self)
        self._mpris.enable()

    def disable_media_player(self) -> None:
        """Unregister the MPRIS media player."""
        if self._mpris is not None:
            self._mpris.disable()

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
        repo_status = (
            Path(__file__).resolve().parent.parent.parent
            / "icons"
            / "hicolor"
            / "scalable"
            / "status"
        )
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
            title=APP_NAME,
            tooltip=self._tray_tooltip(),
            icon_dark_path=icon_dark or icon_fallback,
            icon_light_path=icon_light or icon_fallback,
        )
        self._tray.on_activate = self._on_tray_speak
        self._tray.on_player = self._on_tray_player
        self._tray.set_menu(self._build_tray_menu())
        self._tray.register()
        # Keep app alive when all windows are closed
        self.hold()

    _notif_dismiss_id: int = 0
    _notif_id: int = 0  # D-Bus notification ID
    _notif_text_len: int = 0  # Length of notified text for proportional delay
    _notif_body: str = ""  # Last notification body for re-use on countdown
    _tray_revert_id: int = 0  # Debounce timer for reverting the tray to idle

    def _on_tray_revert(self) -> bool:
        """Debounced revert of the tray to its idle state.

        Runs a short grace period after playback reports IDLE. If a new chunk has
        since resumed (``is_speaking`` True again), this refresh keeps the controls
        shown; only a real end clears them.
        """
        self._tray_revert_id = 0
        self._refresh_tray_playback()
        return False

    def _release_speak_hold(self) -> None:
        if self._speak_hold:
            self._speak_hold = False
            self.release()

    def _on_tts_state_changed(self, state: "TTSState") -> None:
        """Notify tray icon / MPRIS of TTS state changes and process the queue."""
        from config import TTSState

        # Busy: loading the voice or speaking. Both show the playback controls.
        speaking = state in (TTSState.LOADING, TTSState.SPEAKING)

        if state == TTSState.ERROR:
            tts = self._tts_service
            self._speech_queue.clear()
            if self._mpris is not None:
                self._mpris.set_stopped()
            if self._tray is not None:
                self._refresh_tray_playback()
            if self._notif_dismiss_id:
                GLib.source_remove(self._notif_dismiss_id)
                self._notif_dismiss_id = 0
            message = (tts.last_error if tts else "") or _("Could not read the text.")
            self._show_dbus_notification(message, expire_timeout=8000, summary=_("Could not read the text"), icon="dialog-warning")
            self._notif_dismiss_id = GLib.timeout_add(8000, self._dismiss_notification)
            self._release_speak_hold()
            return

        # Shared title + duration estimate for both players.
        spoken = (
            getattr(self._tts_service, "_last_spoken_text", "")
            if self._tts_service
            else ""
        )
        title = spoken[:120] if spoken else _("Reading text")
        # Rough length estimate (~60 ms/char); both players stop exactly when
        # speech ends regardless of the estimate.
        duration_ms = max(2000, len(spoken) * 60) if spoken else 0

        # MPRIS media player (KDE media controls / media keys): only while reading.
        if self._mpris is not None:
            if speaking:
                self._mpris.set_playing(title, duration_ms * 1000)
            else:
                self._mpris.set_stopped()

        if self._tray is None:
            return
        # Pulse the tray icon + update the tooltip and rebuild the context menu
        # (idle → "Read text"; reading → "Playing…" with the controls).
        #
        # Multi-chunk backends (Piper) briefly drop to IDLE between chunks while
        # the next one synthesizes, which would make the playback controls flicker
        # out and back. So show "speaking" immediately, but DEBOUNCE the revert to
        # idle: only clear the controls if playback is still stopped after a short
        # grace period (a real end), not during an inter-chunk gap.
        if speaking:
            if self._tray_revert_id:
                GLib.source_remove(self._tray_revert_id)
                self._tray_revert_id = 0
            self._refresh_tray_playback()
        else:
            if self._tray_revert_id:
                GLib.source_remove(self._tray_revert_id)
            self._tray_revert_id = GLib.timeout_add(700, self._on_tray_revert)

        if state == TTSState.SPEAKING:
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
        elif state == TTSState.IDLE:
            # An explicit Stop also drops what was waiting in the queue.
            if self._tts_service is not None and self._tts_service.stopped_by_user:
                self._speech_queue.clear()
            # Process speech queue (queue mode)
            if self._speech_queue:
                params = self._speech_queue.pop(0)
                GLib.idle_add(lambda p=params: self.tts_service.speak(**p) and False)
                return
            self._release_speak_hold()
            if not self._notif_id:
                return  # nothing was announced (e.g. stopped while loading)

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
                delay_ms,
                self._dismiss_notification,
            )

    _notif_proxy = None

    def _notifications(self):
        """Cached org.freedesktop.Notifications proxy (created once)."""
        if self._notif_proxy is None:
            self._notif_proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES
                | Gio.DBusProxyFlags.DO_NOT_CONNECT_SIGNALS,
                None,
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                None,
            )
        return self._notif_proxy

    def _show_dbus_notification(
        self,
        body: str,
        *,
        expire_timeout: int = 0,
        summary: str = "",
        icon: str = "tts-biglinux",
    ) -> None:
        """Show notification via org.freedesktop.Notifications D-Bus.

        Args:
            body: Notification body text (plain; markup is escaped).
            expire_timeout: Auto-dismiss delay in ms (0 = server decides).
                KDE shows a countdown bar when > 0.
        """
        try:
            # Notification servers may interpret a subset of HTML in the body;
            # the body is the person's text, so it is escaped.
            safe_body = GLib.markup_escape_text(body)
            result = self._notifications().call_sync(
                "Notify",
                GLib.Variant(
                    "(susssasa{sv}i)",
                    (
                        APP_NAME,  # app_name
                        self._notif_id,  # replaces_id (0 = new)
                        icon,  # icon
                        summary or APP_NAME,  # summary
                        safe_body,  # body
                        [],  # actions
                        {},  # hints
                        expire_timeout,  # expire_timeout (ms, 0 = server decides)
                    ),
                ),
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            self._notif_id = result.unpack()[0]
        except Exception as e:
            logger.debug("Notification failed: %s", e)

    def _dismiss_notification(self) -> bool:
        """Close notification via D-Bus after debounce period."""
        self._notif_dismiss_id = 0
        if self._notif_id:
            try:
                self._notifications().call_sync(
                    "CloseNotification",
                    GLib.Variant("(u)", (self._notif_id,)),
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
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
        """Speak selected text (global shortcut / tray "Read text").

        The shortcut is a toggle: pressed while a request is loading or
        speaking, it stops it (README: "press again to stop"). Otherwise the
        playback_mode setting decides what happens to new text:
        - interrupt: stop previous speech, start new
        - queue: enqueue if speaking, auto-play when done
        - simultaneous: play new speech without stopping
        """
        import threading

        from services.clipboard_service import get_selected_text

        tts = self.tts_service
        mode = self.settings.history.playback_mode  # interrupt|queue|simultaneous

        if mode == "interrupt" and tts.is_speaking:
            self._speech_queue.clear()
            tts.stop()
            self._release_speak_hold()
            return

        def _no_text(reason: str) -> bool:
            self._speech_queue.clear()
            if tts.is_speaking:
                tts.stop()
            elif reason in ("wl-clipboard not installed", "xsel or xclip not installed"):
                self._show_dbus_notification(
                    _("Install wl-clipboard (Wayland) or xclip (X11) so selected text can be read."),
                    expire_timeout=8000, summary=_("Could not read the text"), icon="dialog-warning",
                )
            else:
                self._show_dbus_notification(
                    _("Select some text first, then press the shortcut."),
                    expire_timeout=4000, summary=_("No text selected"),
                )
            self._release_speak_hold()
            return False

        def _capture_and_speak() -> None:
            result = get_selected_text()
            logger.debug("Tray speak: captured %d chars (%s)", len(result.text), result.error or "ok")
            if not result.text:
                GLib.idle_add(_no_text, result.error)
                return

            raw_text = result.text
            logger.debug("Tray speak: raw text length=%d, mode=%s", len(raw_text), mode)

            speak_params = dict(text=raw_text, **self.speak_options())

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
                if not tts.is_speaking and tts.state.value != "error":
                    self._release_speak_hold()  # e.g. text empty after processing
                return False

            GLib.idle_add(_do_speak)

        threading.Thread(target=_capture_and_speak, daemon=True).start()

    def speak_options(self) -> dict:
        """Voice and text settings for TTSService.speak(), for every entry point."""
        from services.voice_manager import voice_language

        speech, text = self.settings.speech, self.settings.text
        return dict(
            rate=speech.rate,
            pitch=speech.pitch,
            volume=speech.volume,
            backend=speech.backend,
            voice_id=speech.voice_id,
            language=voice_language(speech.backend, speech.voice_id) or None,
            expand_abbreviations=text.expand_abbreviations,
            process_special_chars=text.process_special_chars,
            process_urls=text.process_urls,
            strip_formatting=text.strip_formatting,
            normalize_numbers=text.normalize_numbers,
            max_chars=text.max_chars,
        )

    def replay_last(self) -> bool:
        """Read the last text again with the current settings (media Play key)."""
        text = self.tts_service.last_text
        return bool(text) and self.tts_service.speak(text, **self.speak_options())

    # Tray menu item IDs
    _TRAY_READ = 1
    _TRAY_SETTINGS = 2
    _TRAY_QUIT = 4
    _TRAY_PAUSE = 12  # Pause / Play (label toggles while paused)
    _TRAY_STOP = 13  # Stop

    def _build_tray_menu(self) -> list[MenuItem]:
        """Build the tray context menu for the current playback state.

        Idle:     Read text / Settings / — / Quit
        Playing:  Pause / Stop / — / Settings / — / Quit
        Paused:   Play  / Stop / — / Settings / — / Quit

        Rows are laid out flat (nested submenus of a tray context menu are
        unreliable on Plasma Wayland) and the full row set is always returned —
        see the comment below on why only `visible` changes between states.
        """
        tts = self._tts_service
        speaking = bool(tts and tts.is_speaking)
        paused = bool(tts and tts.is_paused)

        # The FULL row set is always returned, in a fixed order, and playback
        # state only flips `visible`. The tray helper creates the rows once and
        # then updates properties — structural rebuilds do not propagate reliably
        # through Plasma's DBusMenu (the panel would show a stale menu).
        #
        # While audio plays only two rows are shown: Pause/Stop. Pausing keeps
        # them and turns "Pause" into "Play" (resume from where it stopped);
        # Stop — or the end of the audio — hides them again.
        return [
            MenuItem(
                self._TRAY_READ, _("Read text"), self._on_tray_speak,
                visible=not speaking,
            ),
            MenuItem(
                self._TRAY_PAUSE,
                # pt-BR: "Pause" → "Pausar", "Play" → "Reproduzir" (resume).
                _("Play") if paused else _("Pause"),
                lambda: self._on_tray_player("resume" if paused else "pause"),
                visible=speaking,
            ),
            MenuItem(
                self._TRAY_STOP, _("Stop"),
                lambda: self._on_tray_player("stop"),
                visible=speaking,
            ),
            MenuItem(3, "", separator=True, visible=speaking),
            MenuItem(self._TRAY_SETTINGS, _("Settings"), self._on_tray_settings),
            MenuItem(5, "", separator=True),
            MenuItem(self._TRAY_QUIT, _("Quit"), self._on_tray_quit),
        ]

    def refresh_playback_controls(self) -> None:
        """Tray + MPRIS after a pause/resume (state itself stays SPEAKING)."""
        self._refresh_tray_playback()
        tts = self._tts_service
        if self._mpris is not None and tts is not None and tts.is_speaking:
            self._mpris.set_paused(tts.is_paused)

    def _refresh_tray_playback(self) -> None:
        """Sync the tray icon animation, tooltip and menu with playback state."""
        if self._tray is None:
            return
        tts = self._tts_service
        speaking = bool(tts and tts.is_speaking)
        paused = bool(tts and tts.is_paused)
        label = (_("Paused") if paused else _("Playing…")) if speaking else ""
        settings = Gtk.Settings.get_default()
        animate = settings is None or settings.get_property("gtk-enable-animations")
        self._tray.set_speaking(speaking, label, paused=paused, animate=animate)
        self._tray.set_menu(self._build_tray_menu())

    def _on_tray_player(self, action: str) -> None:
        """Handle player actions from the tray menu / left-click.

        stop → stop; pause/resume → freeze and resume playback in place. The
        tray is refreshed so the icon, tooltip and the Pause/Play label follow
        the new state.
        """
        tts = self.tts_service

        def _run() -> bool:
            if action == "stop":
                tts.stop()
            elif action == "pause":
                tts.pause()
            elif action == "resume":
                tts.resume()
            self.refresh_playback_controls()
            return False

        GLib.idle_add(_run)

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

    def _on_about(self, action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        """Show about dialog."""
        about = Adw.AboutDialog.new()
        about.set_application_name(APP_NAME)
        about.set_application_icon("tts-biglinux")
        about.set_developer_name(_("BigLinux Team"))
        about.set_version(APP_VERSION)
        about.set_developers(APP_DEVELOPERS)
        about.set_copyright(APP_COPYRIGHT)
        about.set_license_type(Gtk.License.GPL_3_0)
        about.set_website(APP_WEBSITE)
        about.set_issue_url(APP_ISSUE_URL)
        about.set_comments(
            "\n\n".join(
                (
                    _(
                        "BigLinux TTS turns selected or typed text into speech with local voices, global shortcuts and precise playback controls."
                    ),
                    _(
                        "BigLinux TTS began as a web-based tool in 2021 and evolved into a native GTK4 application with multiple speech engines, voice management and a Rust audio engine."
                    ),
                )
            )
        )

        # The built-in Troubleshooting page provides copy/save controls for
        # diagnostics that users can attach to an issue report.
        try:
            from services.diagnostics import collect_diagnostics, format_diagnostics

            about.set_debug_info(format_diagnostics(collect_diagnostics(self.settings)))
            about.set_debug_info_filename("biglinux-tts-diagnostic.txt")
        except Exception as e:
            logger.debug("Could not attach diagnostics: %s", e)
        about.present(self._window)

    def _on_quit(self, action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        """Quit the application."""
        logger.info("Quit action triggered")
        if self._tray is not None:
            self.release()
        self.quit()

    # ── Shortcut Registration ────────────────────────────────────────

    def add_shortcut_listener(self, callback) -> None:
        """``callback(ShortcutStatus)`` on the main thread after each registration."""
        self._shortcut_listeners.append(callback)

    def apply_shortcut(self, accel: str) -> None:
        """Register ``accel`` with the desktop in the background.

        The result (registered, conflict or failure) arrives later through
        ``add_shortcut_listener``; nothing claims success before that.
        """
        import threading

        from services import shortcut_service

        self._shortcut_request += 1
        request = self._shortcut_request
        self.shortcut_status = shortcut_service.ShortcutStatus(accel=accel)
        self._notify_shortcut()

        def _work() -> None:
            try:
                status = shortcut_service.register(accel)
            except Exception as e:
                logger.warning("Shortcut registration crashed: %s", e)
                status = shortcut_service.ShortcutStatus(
                    accel=accel, registered=False,
                    message=_("Could not register the shortcut."),
                )
            GLib.idle_add(self._on_shortcut_registered, request, status)

        threading.Thread(target=_work, daemon=True).start()

    def _on_shortcut_registered(self, request: int, status) -> bool:
        if request == self._shortcut_request:  # ignore superseded attempts
            self.shortcut_status = status
            self._notify_shortcut()
        return False

    def _notify_shortcut(self) -> None:
        if self._tray is not None:
            self._tray.set_tooltip(self._tray_tooltip())
        for cb in list(self._shortcut_listeners):
            try:
                cb(self.shortcut_status)
            except Exception as e:
                logger.warning("Shortcut listener error: %s", e)

    def _tray_tooltip(self) -> str:
        from services.shortcut_service import display_text

        shortcut = display_text(self.settings.shortcut.keybinding)
        if shortcut and self.shortcut_status.registered is not False:
            return _("Select text and press {shortcut} to hear it").format(shortcut=shortcut)
        return _("Text-to-speech assistant")
