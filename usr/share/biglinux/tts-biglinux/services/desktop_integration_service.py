"""
Desktop integration: global shortcuts and the .desktop entry they launch.

Separates OS/DE specific DBus/X11/Wayland behavior from the UI layer.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from utils.i18n import _

logger = logging.getLogger(__name__)


class DesktopIntegrationService:
    """Desktop integration: global shortcuts (KDE, GNOME, Xfce, Cinnamon) and the launcher entry."""

    @staticmethod
    def gtk_accel_to_kde(accel: str) -> str:
        """Convert GTK accelerator string to KDE format."""
        kde = accel
        kde = kde.replace("<Control>", "Ctrl+")
        kde = kde.replace("<Shift>", "Shift+")
        kde = kde.replace("<Alt>", "Alt+")
        kde = kde.replace("<Super>", "Meta+")
        if "+" in kde:
            parts = kde.rsplit("+", 1)
            kde = parts[0] + "+" + parts[1].upper()
        else:
            kde = kde.upper()
        return kde

    @staticmethod
    def block_global_shortcuts(block: bool) -> None:
        """Block or unblock all global shortcuts via KGlobalAccel D-Bus."""
        try:
            subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--type=method_call",
                    "--dest=org.kde.kglobalaccel",
                    "/kglobalaccel",
                    "org.kde.KGlobalAccel.blockGlobalShortcuts",
                    f"boolean:{'true' if block else 'false'}",
                ],
                timeout=3,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.debug("Global shortcuts %s", "blocked" if block else "unblocked")
        except (OSError, subprocess.TimeoutExpired):
            logger.warning(
                "Could not %s global shortcuts", "block" if block else "unblock"
            )

    @staticmethod
    def reload_kglobalaccel() -> None:
        """Force KGlobalAccel to reload shortcut configuration."""
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
                timeout=3,
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
                timeout=3,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
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
                    timeout=3,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
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
                timeout=3,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    @staticmethod
    def radical_dbus_cleanup() -> None:
        """Explicitly unregister legacy components from KGlobalAccel via DBus."""
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
                except Exception:
                    pass

    @staticmethod
    def unregister_shortcut_from_memory() -> None:
        """Unregister our component from KGlobalAccel in-memory cache."""
        from config import APP_ID
        comp = f"{APP_ID}.desktop"
        try:
            subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.kde.kglobalaccel",
                    "--object-path",
                    "/kglobalaccel",
                    "--method",
                    "org.kde.KGlobalAccel.unregister",
                    f"'{comp}'",
                    "'_launch'",
                ],
                timeout=2,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--type=method_call",
                    "--dest=org.kde.kglobalaccel",
                    "/kglobalaccel",
                    "org.kde.KGlobalAccel.unregister",
                    f"string:{comp}",
                    "string:_launch",
                ],
                timeout=2,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    @staticmethod
    def purge_stale_kde_components(keep: str = "biglinux_tts_speak_desktop") -> None:
        """Remove stale KGlobalAccel components that fight over our shortcut.

        Older versions registered the speak shortcut under component names such
        as ``bigtts.desktop`` / ``tts-speak.desktop``. Those linger in
        kglobalacceld's *in-memory* registry (the config file may already say
        ``none``) and their ``.desktop`` targets no longer exist. When several
        components claim the same key (e.g. Alt+V), KGlobalAccel cannot route
        the press to our launcher, so the shortcut silently fails when the app
        is not already running. ``Component.cleanUp`` drops each zombie so the
        live ``biglinux-tts-speak.desktop`` component owns the key alone.

        kglobalacceld also loads every launcher in ~/.local/share/kglobalaccel/
        at each login (System Settings copies one there when a shortcut is added
        by hand), so old copies of ours are deleted too — otherwise the zombies
        come back with their default Alt+V before the app ever starts.
        """
        import shutil

        DesktopIntegrationService.remove_stale_kde_launchers()

        # D-Bus object-path names (dots/dashes → underscores) of components
        # historically used for the speak shortcut. Never touch ``keep``.
        stale = [
            "bigtts_desktop",
            "tts_speak_desktop",
            "br_com_biglinux_tts_desktop",
        ]
        qdbus = next((c for c in ("qdbus6", "qdbus") if shutil.which(c)), None)
        if not qdbus:
            return
        for name in stale:
            if name == keep:
                continue
            try:
                subprocess.run(
                    [
                        qdbus,
                        "org.kde.kglobalaccel",
                        f"/component/{name}",
                        "org.kde.kglobalaccel.Component.cleanUp",
                    ],
                    timeout=3,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        logger.debug("Purged stale KDE shortcut components (kept %s)", keep)

    # Launchers older versions (or System Settings) left in the kglobalaccel
    # data dir; each one is a separate component claiming the speak shortcut.
    _STALE_KDE_LAUNCHERS = ("bigtts.desktop", "tts-speak.desktop", "br.com.biglinux.tts.desktop")

    @staticmethod
    def _kde_launcher_dir() -> Path:
        return Path.home() / ".local" / "share" / "kglobalaccel"

    @staticmethod
    def _runs_this_app(command: str) -> bool:
        """The Exec program (not an argument) is one of our launchers."""
        import shlex

        try:
            argv = shlex.split(command)
        except ValueError:
            return False
        return bool(argv) and Path(argv[0]).name in ("biglinux-tts", "biglinux-tts-speak")

    @staticmethod
    def _main_exec(text: str) -> str:
        """Exec of the [Desktop Entry] group (actions have their own Exec)."""
        group = ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                group = line
            elif group == "[Desktop Entry]" and line.startswith("Exec="):
                return line[5:]
        return ""

    @classmethod
    def remove_stale_kde_launchers(cls) -> list[Path]:
        """Delete our old launchers from ~/.local/share/kglobalaccel/.

        Only files whose Exec runs this app are removed, so a user's own
        launcher that happens to share a name is left alone.
        """
        removed = []
        for name in cls._STALE_KDE_LAUNCHERS:
            path = cls._kde_launcher_dir() / name
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not cls._runs_this_app(cls._main_exec(text)):
                continue
            try:
                path.unlink()
            except OSError as e:
                logger.warning("Could not remove stale shortcut launcher %s: %s", path, e)
                continue
            removed.append(path)
            logger.info("Removed stale shortcut launcher %s", path)
        return removed

    @staticmethod
    def ensure_desktop_file(kde_key: str) -> Path:
        """Ensure the shortcut desktop file exists locally with current keybinding."""
        local_apps = Path.home() / ".local" / "share" / "applications"
        # Must match the component name in kglobalshortcutsrc
        desktop_dst = local_apps / "biglinux-tts-speak.desktop"

        exec_path = DesktopIntegrationService._exec_path_for_speak()
        # Name/GenericName are what the desktop's shortcut settings list.
        # (_() outside the f-string: xgettext does not look inside f-strings.)
        generic_name = _("Read or stop the selected text")
        content = f"""[Desktop Entry]
Type=Application
Exec={exec_path}
Icon=tts-biglinux
Categories=Utility;Accessibility;
StartupNotify=false
NoDisplay=true
X-KDE-Shortcuts={kde_key}
Name=BigLinux TTS
GenericName={generic_name}
"""
        desktop_dst.parent.mkdir(parents=True, exist_ok=True)
        desktop_dst.write_text(content, encoding="utf-8")
        # kglobalacceld looks in its own data dir before applications/, so a
        # copy there (made by System Settings) would keep an old Exec/default.
        kde_copy = DesktopIntegrationService._kde_launcher_dir() / desktop_dst.name
        if kde_copy.is_file():
            try:
                kde_copy.write_text(content, encoding="utf-8")
            except OSError as e:
                logger.warning("Could not update %s: %s", kde_copy, e)
        return desktop_dst

    @classmethod
    def write_kde_shortcut_config(cls, accel: str) -> None:
        """Fallback when KGlobalAccel is unreachable: write kglobalshortcutsrc and reload."""
        kde_shortcut = cls.gtk_accel_to_kde(accel)
        logger.info("Updating KDE shortcut to: %s", kde_shortcut)

        # 1. Update local .desktop file with the new X-KDE-Shortcuts
        cls.ensure_desktop_file(kde_shortcut)

        # 2. Unregister components of older versions first
        cls.radical_dbus_cleanup()

        from config import APP_ID
        # Groups to clean and register in (Plasma 5 and 6)
        groups = [
            ("services", f"{APP_ID}.desktop"),
            ("", f"{APP_ID}.desktop"),
            ("services", "biglinux-tts-speak.desktop"),
            ("", "biglinux-tts-speak.desktop"),
            ("", "bigtts.desktop"),
        ]

        import shutil

        registry_cmds = ["kwriteconfig6", "kwriteconfig5", "kwriteconfig"]

        for group_prefix, group_name in groups:
            for kcmd in registry_cmds:
                if not shutil.which(kcmd):
                    continue
                try:
                    cmd = [kcmd, "--file", "kglobalshortcutsrc"]
                    if group_prefix:
                        cmd.extend(["--group", group_prefix])
                    cmd.extend(["--group", group_name, "--key", "_launch"])

                    if "bigtts" in group_name or "br.com.biglinux.tts" in group_name:
                        subprocess.run(cmd + ["--delete"], timeout=2, check=False)
                        continue

                    if kde_shortcut.lower() != "none":
                        # [services] entries hold only the key(s), separated by
                        # TAB. The "active,default,name" triple belongs to
                        # component groups — written here it would be parsed as
                        # a multi-chord sequence (press Alt+V, then Alt+V).
                        if group_prefix == "services":
                            val = kde_shortcut
                        else:
                            name = _("Read or stop the selected text")
                            val = f"{kde_shortcut},{kde_shortcut},{name}"
                        subprocess.run(cmd + [val], timeout=2, check=False)
                    else:
                        subprocess.run(cmd + ["--delete"], timeout=2, check=False)
                except Exception:
                    pass

        # 3. Rebuild system caches
        cls.update_desktop_database()

        # 4. Force kglobalaccel to re-read
        cls.reload_kglobalaccel()

        # 5. Unregister stale in-memory entry, then let kglobalaccel re-read
        cls.unregister_shortcut_from_memory()
        time.sleep(0.15)
        cls.reload_kglobalaccel()

    @staticmethod
    def update_desktop_database() -> None:
        """Update the desktop file database so KDE picks up changes."""
        local_apps = Path.home() / ".local" / "share" / "applications"
        try:
            subprocess.run(
                ["update-desktop-database", str(local_apps)], timeout=5, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        for cmd in ["kbuildsycoca6", "kbuildsycoca5"]:
            try:
                subprocess.run(
                    [cmd, "--noincremental"],
                    timeout=10,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

    # ── Cross-DE Shortcut Support ────────────────────────────────────

    @staticmethod
    def detect_desktop_environment() -> str:
        """Detect current desktop environment from XDG_CURRENT_DESKTOP.

        Returns one of: 'kde', 'gnome', 'xfce', 'cinnamon', 'unknown'.
        """
        xdg = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if "kde" in xdg or "plasma" in xdg:
            return "kde"
        if "gnome" in xdg or "unity" in xdg:
            return "gnome"
        if "xfce" in xdg:
            return "xfce"
        if "cinnamon" in xdg or "x-cinnamon" in xdg:
            return "cinnamon"
        # Fallback: check DESKTOP_SESSION
        session = os.environ.get("DESKTOP_SESSION", "").lower()
        for key, val in [
            ("plasma", "kde"), ("kde", "kde"),
            ("gnome", "gnome"), ("ubuntu", "gnome"),
            ("xfce", "xfce"), ("xubuntu", "xfce"),
            ("cinnamon", "cinnamon"),
        ]:
            if key in session:
                return val
        return "unknown"

    @staticmethod
    def _exec_path_for_speak() -> str:
        """The command the global shortcut runs (a source checkout uses its own)."""
        # services/ → tts-biglinux/ → biglinux/ → share/ → usr/ → checkout root
        repo_root = Path(__file__).resolve().parents[5]
        script = repo_root / "usr" / "bin" / "biglinux-tts-speak"
        if script.exists() and (repo_root / ".git").exists():
            return str(script)
        return "/usr/bin/biglinux-tts-speak"

    @classmethod
    def register_gnome_shortcut(cls, accel: str, exec_path: str) -> bool:
        """Register custom keybinding in GNOME via gsettings.

        GNOME stores custom shortcuts under:
          org.gnome.settings-daemon.plugins.media-keys custom-keybindings
        Each binding is a separate dconf path: .../customN/
        """
        schema = "org.gnome.settings-daemon.plugins.media-keys"
        base_path = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
        binding_name = "BigLinux TTS Speak"
        slot_path = f"{base_path}/biglinux-tts/"

        # Check if our slot already exists
        try:
            result = subprocess.run(
                ["gsettings", "get", schema, "custom-keybindings"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                logger.warning("gsettings not available for GNOME shortcuts")
                return False
            # Parse the list (GVariant format: ['path1', 'path2'])
            raw = result.stdout.strip()
            if raw == "@as []" or raw == "[]":
                current_paths: list[str] = []
            else:
                # GVariant uses single-quotes; parse manually
                current_paths = [
                    p.strip().strip("'\"")
                    for p in raw.strip("[]").split(",")
                    if p.strip()
                ]
        except (OSError, subprocess.TimeoutExpired):
            logger.warning("Failed to read GNOME custom-keybindings")
            return False

        # Add our slot path if missing
        if slot_path not in current_paths:
            current_paths.append(slot_path)
            paths_str = "[" + ", ".join(f"'{p}'" for p in current_paths) + "]"
            try:
                subprocess.run(
                    ["gsettings", "set", schema, "custom-keybindings", paths_str],
                    timeout=5, check=True,
                )
            except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                logger.error("Failed to register GNOME keybinding slot: %s", e)
                return False

        # Set the binding properties
        custom_schema = f"{schema}.custom-keybinding"
        custom_path = slot_path
        cmds = [
            ["gsettings", "set", f"{custom_schema}:{custom_path}", "name", binding_name],
            ["gsettings", "set", f"{custom_schema}:{custom_path}", "command", exec_path],
            ["gsettings", "set", f"{custom_schema}:{custom_path}", "binding", accel],
        ]
        for cmd in cmds:
            try:
                subprocess.run(cmd, timeout=5, check=True)
            except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                logger.error("Failed to set GNOME keybinding property: %s", e)
                return False

        logger.info("GNOME shortcut registered: %s → %s", accel, exec_path)
        return True

    @classmethod
    def register_xfce_shortcut(cls, accel: str, exec_path: str) -> bool:
        """Register custom keybinding in XFCE via xfconf-query.

        XFCE stores shortcuts in xfce4-keyboard-shortcuts channel.
        Path format: /commands/custom/<accel>
        """
        import shutil

        if not shutil.which("xfconf-query"):
            logger.warning("xfconf-query not available — cannot register XFCE shortcut")
            return False

        # Convert GTK accel to XFCE format — XFCE uses same style but
        # may need uppercase key name
        xfce_accel = accel  # '<Alt>v' works in XFCE

        channel = "xfce4-keyboard-shortcuts"
        prop_path = f"/commands/custom/{xfce_accel}"

        def is_ours(command: str) -> bool:
            # The speak launcher only — not a user's own binding that opens
            # the app window (/usr/bin/biglinux-tts).
            return command.strip().split("/")[-1] == "biglinux-tts-speak"

        try:
            result = subprocess.run(
                ["xfconf-query", "-c", channel, "-l", "-v"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("Failed to read XFCE shortcuts: %s", e)
            return False
        if result.returncode != 0:
            # Without the list, another command on the key could be overwritten.
            logger.error("Failed to read XFCE shortcuts (xfconf-query exit %d)", result.returncode)
            return False
        bindings = {}
        for line in result.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].startswith("/commands/custom/"):
                bindings[parts[0]] = parts[1].strip()

        current = bindings.get(prop_path)
        if current is not None and current != exec_path and not is_ours(current):
            logger.warning("XFCE shortcut %s is already used by %s", xfce_accel, current)
            return False

        # Set the new binding first, then drop our old ones: an app that quits
        # in between never leaves the user without a shortcut. Already right:
        # nothing is written (no churn at every start).
        if current != exec_path:
            try:
                subprocess.run(
                    [
                        "xfconf-query", "-c", channel,
                        "-p", prop_path,
                        "-n", "-t", "string", "-s", exec_path,
                    ],
                    timeout=5, check=True,
                )
            except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                logger.error("Failed to register XFCE shortcut: %s", e)
                return False
        for prop, command in bindings.items():
            if prop != prop_path and is_ours(command):
                try:
                    subprocess.run(
                        ["xfconf-query", "-c", channel, "-p", prop, "-r"],
                        timeout=3, check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
        logger.info("XFCE shortcut registered: %s → %s", xfce_accel, exec_path)
        return True

    @classmethod
    def register_cinnamon_shortcut(cls, accel: str, exec_path: str) -> bool:
        """Register custom keybinding in Cinnamon via gsettings.

        Cinnamon uses a numbered list of custom keybindings under:
          org.cinnamon.desktop.keybindings custom-list
        Each binding: org.cinnamon.desktop.keybindings.custom-keybinding:/…/customN/
        """
        binding_name = "BigLinux TTS Speak"
        list_schema = "org.cinnamon.desktop.keybindings"
        custom_schema = "org.cinnamon.desktop.keybindings.custom-keybinding"
        base_path = "/org/cinnamon/desktop/keybindings/custom-keybindings"

        # Read current custom-list
        try:
            result = subprocess.run(
                ["gsettings", "get", list_schema, "custom-list"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                logger.warning("gsettings not available for Cinnamon shortcuts")
                return False

            raw = result.stdout.strip()
            if raw == "@as []" or raw == "[]":
                current_list: list[str] = []
            else:
                current_list = [
                    p.strip().strip("'\"")
                    for p in raw.strip("[]").split(",")
                    if p.strip()
                ]
        except (OSError, subprocess.TimeoutExpired):
            return False

        # Check if we already have a slot — look for existing biglinux entry
        our_slot: str | None = None
        for slot_id in current_list:
            slot_path = f"{base_path}/{slot_id}/"
            try:
                result = subprocess.run(
                    ["gsettings", "get", f"{custom_schema}:{slot_path}", "name"],
                    capture_output=True, text=True, timeout=3,
                )
                if binding_name in result.stdout:
                    our_slot = slot_id
                    break
            except (OSError, subprocess.TimeoutExpired):
                continue

        # Allocate new slot if needed
        if our_slot is None:
            # Find next available number
            existing_nums = set()
            for s in current_list:
                if s.startswith("custom"):
                    try:
                        existing_nums.add(int(s.removeprefix("custom")))
                    except ValueError:
                        pass
            next_num = 0
            while next_num in existing_nums:
                next_num += 1
            our_slot = f"custom{next_num}"
            current_list.append(our_slot)

            # Update the list
            list_str = "[" + ", ".join(f"'{s}'" for s in current_list) + "]"
            try:
                subprocess.run(
                    ["gsettings", "set", list_schema, "custom-list", list_str],
                    timeout=5, check=True,
                )
            except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                logger.error("Failed to update Cinnamon custom-list: %s", e)
                return False

        # Set binding properties
        slot_path = f"{base_path}/{our_slot}/"
        cmds = [
            ["gsettings", "set", f"{custom_schema}:{slot_path}", "name", binding_name],
            ["gsettings", "set", f"{custom_schema}:{slot_path}", "command", exec_path],
            ["gsettings", "set", f"{custom_schema}:{slot_path}", "binding", f"['{accel}']"],
        ]
        for cmd in cmds:
            try:
                subprocess.run(cmd, timeout=5, check=True)
            except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                logger.error("Failed to set Cinnamon keybinding: %s", e)
                return False

        logger.info("Cinnamon shortcut registered: %s → %s", accel, exec_path)
        return True

    @classmethod
    def register_shortcut_for_current_de(cls, accel: str) -> bool:
        """Register the global shortcut using the appropriate DE mechanism.

        Detects the current DE and calls the correct registration method.
        On KDE, writes the shortcut configuration (write_kde_shortcut_config).
        Returns True if successfully registered.
        """
        de = cls.detect_desktop_environment()
        exec_path = cls._exec_path_for_speak()
        logger.info("Registering shortcut '%s' for DE: %s", accel, de)

        if de == "kde":
            cls.write_kde_shortcut_config(accel)
            return True
        elif de == "gnome":
            return cls.register_gnome_shortcut(accel, exec_path)
        elif de == "xfce":
            return cls.register_xfce_shortcut(accel, exec_path)
        elif de == "cinnamon":
            return cls.register_cinnamon_shortcut(accel, exec_path)
        # Unknown desktop: writing GNOME's gsettings could "succeed" with no
        # daemon listening, and the app would promise a shortcut that never
        # fires. The user adds it in the desktop's own keyboard settings.
        logger.info("Unknown desktop '%s': shortcut must be added by hand", de)
        return False
