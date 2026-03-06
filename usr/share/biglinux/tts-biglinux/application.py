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

        # Explicitly set color scheme to prevent warnings from KDE injected settings
        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)

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
            candidate = (
                f"{prefix}/icons/hicolor/scalable/status/tts-biglinux-symbolic.svg"
            )
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
        self._tray.set_menu(
            [
                MenuItem(1, _("Settings"), self._on_tray_settings),
                MenuItem(2, "", separator=True),
                MenuItem(3, _("Quit"), self._on_tray_quit),
            ]
        )
        self._tray.register()
        # Keep app alive when all windows are closed
        self.hold()

    def _on_window_close_request(self, window: Gtk.Window) -> bool:
        """Hide window to tray instead of quitting."""
        if self._tray is not None:
            if not self.settings.window.tray_warning_shown:
                from gi.repository import Adw

                def _on_dialog_response(
                    dialog: Adw.MessageDialog, response: str
                ) -> None:
                    window.set_visible(False)
                    self.settings.window.tray_warning_shown = True
                    self.settings_service.save()

                dialog = Adw.MessageDialog(
                    heading=_("Minimized to System Tray"),
                    body=_(
                        "BigLinux TTS Speak is still running in the background.\nYou can access it anytime from the system tray icon."
                    ),
                    transient_for=window,
                )
                dialog.add_response("ok", _("OK"))
                dialog.set_default_response("ok")
                dialog.connect("response", _on_dialog_response)
                dialog.present()
            else:
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
            logger.debug(
                "Tray speak: processed text length=%d",
                len(processed) if processed else 0,
            )
            if not processed:
                return

            speech = self.settings.speech
            logger.debug(
                "Tray speak: backend=%s, voice=%s", speech.backend, speech.voice_id
            )

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

    def _on_about(self, action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
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

    def _on_quit(self, action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
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

        # ── Smart Path Detection ──────────────────────────────────────
        # Use script from Git repo if running from there, otherwise system path
        repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        git_script = repo_root / "usr" / "bin" / "biglinux-tts-speak"
        if git_script.exists():
            exec_path = str(git_script)
            logger.info("Using development script from Git: %s", exec_path)
        else:
            exec_path = "/usr/bin/biglinux-tts-speak"

        # ── Zombie Nuke ──────────────────────────────────────────────
        # These old files in ~/.local/share/applications/ often have Alt+V
        # hardcoded in X-KDE-Shortcuts and override everything.
        local_apps = Path.home() / ".local" / "share" / "applications"
        zombies = ["tts-speak.desktop", "bigtts.desktop"]
        for z in zombies:
            z_path = local_apps / z
            if z_path.exists():
                try:
                    z_path.unlink()
                    logger.info("Deleted zombie desktop file: %s", z_path)
                    # Also unregister from memory
                    comp_name = z
                    for dbus_cmd in [
                        ["qdbus6"],
                        ["qdbus"],
                        ["dbus-send", "--session", "--dest=org.kde.kglobalaccel"],
                    ]:
                        if "dbus-send" in dbus_cmd:
                            subprocess.run(
                                dbus_cmd
                                + [
                                    "/kglobalaccel",
                                    "org.kde.KGlobalAccel.unregister",
                                    f"string:{comp_name}",
                                    "string:_launch",
                                ],
                                timeout=1,
                                stderr=subprocess.DEVNULL,
                            )
                        else:
                            subprocess.run(
                                dbus_cmd
                                + [
                                    "org.kde.kglobalaccel",
                                    "/kglobalaccel",
                                    "org.kde.KGlobalAccel.unregister",
                                    comp_name,
                                    "_launch",
                                ],
                                timeout=1,
                                stderr=subprocess.DEVNULL,
                            )
                except:
                    pass

        # ── Radical Cleanup ──────────────────────────────────────────
        self._radical_dbus_cleanup()

        # Groups to clean and register in
        groups = [
            ("services", "biglinux-tts-speak.desktop"),  # Plasma 6
            ("", "biglinux-tts-speak.desktop"),  # Plasma 5
            ("services", "br.com.biglinux.tts.desktop"),  # Potential UI conflict
            ("", "bigtts.desktop"),  # Legacy
        ]

        # Ensure the desktop file exists unconditionally in local apps
        local_apps = Path.home() / ".local" / "share" / "applications"
        desktop_dst = local_apps / "biglinux-tts-speak.desktop"

        content = f"""[Desktop Entry]
Type=Application
Exec={exec_path}
Icon=tts-biglinux
Categories=Utility;Accessibility;
StartupNotify=false
NoDisplay=true
X-KDE-Shortcuts={kde_shortcut}
Name=BigLinux TTS Speak
GenericName=Speech or stop selected text
GenericName[pt_BR]=Narrador de texto

Actions=SoftwareRender;AmdRender;IntegratedRender;

[Desktop Action SoftwareRender]
Name=Software Render
Exec=SoftwareRender {exec_path}

[Desktop Action AmdRender]
Name=Amd Render
Exec=AmdRender {exec_path}

[Desktop Action IntegratedRender]
Name=Integrated Render
Exec=IntegratedRender {exec_path}
"""
        try:
            desktop_dst.parent.mkdir(parents=True, exist_ok=True)
            desktop_dst.write_text(content, encoding="utf-8")
            # Update database
            subprocess.run(
                ["update-desktop-database", str(local_apps)], timeout=2, check=False
            )
        except OSError as e:
            logger.warning("Could not write desktop file: %s", e)

        # Registry commands
        registry_cmds = ["kwriteconfig6", "kwriteconfig5", "kwriteconfig"]

        # NUKE 'Alt+V' specifically from anywhere it might be hiding
        import shutil

        nuke_targets = [
            "khotkeys",
            "biglinux-tts-speak.desktop",
            "bigtts.desktop",
            "tts-speak.desktop",
            "tts_speak_desktop",
            "plasmashell",
        ]
        for n_group in nuke_targets:
            for kcmd in registry_cmds:
                if shutil.which(kcmd):
                    subprocess.run(
                        [
                            kcmd,
                            "--file",
                            "kglobalshortcutsrc",
                            "--group",
                            n_group,
                            "--key",
                            "_launch",
                            "--delete",
                        ],
                        timeout=1,
                        stderr=subprocess.DEVNULL,
                    )
                    subprocess.run(
                        [
                            kcmd,
                            "--file",
                            "kglobalshortcutsrc",
                            "--group",
                            n_group,
                            "--key",
                            "Launch tts-biglinux",
                            "--delete",
                        ],
                        timeout=1,
                        stderr=subprocess.DEVNULL,
                    )

        if kde_shortcut.lower() == "none":
            logger.info("Shortcut is 'none', skipping registration.")
            # Still reload to ensure old ones are gone
            self._reload_kglobalaccel()
            return

        for group_prefix, group_name in groups:
            for kcmd in registry_cmds:
                if not shutil.which(kcmd):
                    continue
                try:
                    cmd = [kcmd, "--file", "kglobalshortcutsrc"]
                    if group_prefix:
                        cmd.extend(["--group", group_prefix])
                    cmd.extend(["--group", group_name, "--key", "_launch"])
                    # If it's a cleanup target, delete it first
                    if "bigtts" in group_name or "br.com.biglinux.tts" in group_name:
                        subprocess.run(cmd + ["--delete"], timeout=2, check=False)
                        continue

                    # Register new shortcut with COMMAS (most stable separator for kwriteconfig)
                    val = f"{kde_shortcut},{kde_shortcut},Speech or stop selected text"
                    subprocess.run(cmd + [val], timeout=2, check=False)
                except:
                    pass

        # Rebuild sycoca (KDE service cache)
        for scmd in ["kbuildsycoca6", "kbuildsycoca5"]:
            try:
                subprocess.run(
                    [scmd, "--noincremental"],
                    timeout=5,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except:
                pass

        # Notify systems
        self._reload_kglobalaccel()

        # ── Real-Time DBus Injection ────────────────────────────────
        # Force the change in memory so it works WITHOUT log out
        self._inject_shortcut_dbus(kde_shortcut)

    @staticmethod
    def _kde_shortcut_to_qt_keycode(kde_shortcut: str) -> int:
        """Convert a KDE shortcut string (e.g. 'Alt+V') to Qt key code integer.

        Qt combines modifier flags and key value into a single int:
          Alt   = 0x08000000
          Ctrl  = 0x04000000
          Shift = 0x02000000
          Meta  = 0x10000000
        Letter keys use their uppercase ASCII value (e.g. V = 0x56).
        Function keys use Qt::Key_F1 = 0x01000030, F2 = 0x01000031, etc.
        """
        code = 0
        if "Alt+" in kde_shortcut:
            code |= 0x08000000
        if "Ctrl+" in kde_shortcut:
            code |= 0x04000000
        if "Shift+" in kde_shortcut:
            code |= 0x02000000
        if "Meta+" in kde_shortcut:
            code |= 0x10000000

        key = kde_shortcut.split("+")[-1].upper()

        # Single character key — use ASCII value (matches Qt::Key_A..Z, 0..9)
        if len(key) == 1 and key.isascii():
            code |= ord(key)
        # Function keys F1–F35
        elif key.startswith("F") and key[1:].isdigit():
            fn = int(key[1:])
            if 1 <= fn <= 35:
                code |= 0x01000030 + (fn - 1)
        # Common named keys
        else:
            named = {
                "SPACE": 0x20,
                "TAB": 0x01000001,
                "RETURN": 0x01000004,
                "ENTER": 0x01000005,
                "BACKSPACE": 0x01000003,
                "ESCAPE": 0x01000000,
                "DELETE": 0x01000007,
                "INSERT": 0x01000006,
                "HOME": 0x01000010,
                "END": 0x01000011,
                "PAGEUP": 0x01000016,
                "PAGEDOWN": 0x01000017,
                "LEFT": 0x01000012,
                "UP": 0x01000013,
                "RIGHT": 0x01000014,
                "DOWN": 0x01000015,
                "PRINT": 0x01000009,
                "PAUSE": 0x01000008,
                "CAPSLOCK": 0x01000024,
                "NUMLOCK": 0x01000025,
                "SCROLLLOCK": 0x01000026,
            }
            code |= named.get(key, 0)
        return code

    @staticmethod
    def _inject_shortcut_dbus_static(kde_shortcut: str) -> None:
        """Inject the shortcut directly into KGlobalAccel memory via DBus.

        Uses gdbus call with the correct Plasma 6 API:
          setShortcutKeys(as action_id, a(iiii) keys)
        where action_id = [component, action, friendlyName, friendlyDesc]
        and each key tuple = (qt_keycode, 0, 0, 0).
        """
        import subprocess

        qt_code = TTSApplication._kde_shortcut_to_qt_keycode(kde_shortcut)
        if qt_code == 0:
            logger.warning("Could not compute Qt key code for '%s'", kde_shortcut)
            return

        comp = "biglinux-tts-speak.desktop"
        action_id = (
            f"['{comp}', '_launch', "
            f"'BigLinux TTS Speak', 'Speech or stop selected text']"
        )
        keys = f"[({qt_code}, 0, 0, 0)]"

        # Method 1: gdbus call — supports complex GVariant types properly
        try:
            result = subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.kde.kglobalaccel",
                    "--object-path",
                    "/kglobalaccel",
                    "--method",
                    "org.kde.KGlobalAccel.setShortcutKeys",
                    action_id,
                    keys,
                ],
                timeout=3,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                logger.info(
                    "Shortcut injected via gdbus setShortcutKeys: %s (Qt code %d)",
                    kde_shortcut,
                    qt_code,
                )
                return
            logger.debug(
                "gdbus setShortcutKeys returned %d: %s",
                result.returncode,
                result.stderr.strip(),
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.debug("gdbus setShortcutKeys failed: %s", e)

        # Method 2: Fallback using qdbus6 / qdbus
        for qcmd in ["qdbus6", "qdbus"]:
            try:
                subprocess.run(
                    [
                        qcmd,
                        "org.kde.kglobalaccel",
                        "/kglobalaccel",
                        "org.kde.KGlobalAccel.setShortcutKeys",
                        comp,
                        "_launch",
                        "BigLinux TTS Speak",
                        "Speech or stop selected text",
                        str(qt_code),
                        "0",
                        "0",
                        "0",
                    ],
                    timeout=3,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _inject_shortcut_dbus(self, kde_shortcut: str) -> None:
        """Instance wrapper for the static injection."""
        self._inject_shortcut_dbus_static(kde_shortcut)

    @staticmethod
    def _reload_kglobalaccel() -> None:
        """Force KGlobalAccel to reload shortcut configuration."""
        import subprocess
        import time

        # Method 1: block/unblock cycle forces re-read
        try:
            subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--type=method_call",
                    "--dest=org.kde.kglobalaccel",
                    "/kglobalaccel",
                    "org.kde.KGlobalAccel.blockGlobalShortcuts",
                    "boolean:true",
                ],
                timeout=2,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.1)
            subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--type=method_call",
                    "--dest=org.kde.kglobalaccel",
                    "/kglobalaccel",
                    "org.kde.KGlobalAccel.blockGlobalShortcuts",
                    "boolean:false",
                ],
                timeout=2,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except:
            pass

        # Method 2: Plasma 6/5 reparseConfiguration
        for cmd in ["qdbus6", "qdbus"]:
            try:
                subprocess.run(
                    [
                        cmd,
                        "org.kde.kglobalaccel",
                        "/kglobalaccel",
                        "org.kde.KGlobalAccel.reparseConfiguration",
                    ],
                    timeout=2,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except:
                pass

        # Method 3: also notify KGlobalSettings (Legacy)
        try:
            subprocess.run(
                [
                    "dbus-send",
                    "--type=signal",
                    "--session",
                    "/KGlobalSettings",
                    "org.kde.KGlobalSettings.notifyChange",
                    "int32:3",
                    "int32:0",
                ],
                timeout=2,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except:
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
            ("biglinux-tts-speak.desktop", "_launch"),
            ("biglinux-tts-speak.desktop", "IntegratedRender"),
            ("biglinux-tts-speak.desktop", "SoftwareRender"),
            ("biglinux-tts-speak.desktop", "AmdRender"),
        ]
        for comp, action in zombies:
            for dbus_cmd in [
                ["qdbus6"],
                ["qdbus"],
                [
                    "dbus-send",
                    "--session",
                    "--type=method_call",
                    "--dest=org.kde.kglobalaccel",
                ],
            ]:
                try:
                    if "dbus-send" in dbus_cmd:
                        subprocess.run(
                            dbus_cmd
                            + [
                                "/kglobalaccel",
                                "org.kde.KGlobalAccel.unregister",
                                f"string:{comp}",
                                f"string:{action}",
                            ],
                            timeout=1,
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                        )
                    else:
                        subprocess.run(
                            dbus_cmd
                            + [
                                "org.kde.kglobalaccel",
                                "/kglobalaccel",
                                "org.kde.KGlobalAccel.unregister",
                                comp,
                                action,
                            ],
                            timeout=1,
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                        )
                except:
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
                    "qdbus6",
                    "org.kde.kded6",
                    "/kded",
                    "org.kde.kded6.loadedModules",
                ],
                capture_output=True,
                text=True,
                timeout=3,
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
                    "dbus-send",
                    "--session",
                    "--type=method_call",
                    "--dest=org.kde.kded6",
                    "/modules/khotkeys",
                    "org.kde.khotkeys.reread_configuration",
                ],
                timeout=3,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
