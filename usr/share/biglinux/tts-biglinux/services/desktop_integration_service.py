"""
Service to handle desktop integration logic, such as KDE shortcuts and launcher pins.

Separates OS/DE specific DBus/X11/Wayland behavior from the UI layer.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class DesktopIntegrationService:
    """Handles deep OS integration (KDE global shortcuts, desktop files, launchers)."""

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
    def ensure_desktop_file(kde_key: str) -> Path:
        """Ensure the shortcut desktop file exists locally with current keybinding."""
        local_apps = Path.home() / ".local" / "share" / "applications"
        # Must match the component name in kglobalshortcutsrc
        desktop_dst = local_apps / "biglinux-tts-speak.desktop"

        # Dynamic path detection for the executable script
        # services/ → tts-biglinux/ → biglinux/ → share/ → usr/ → repo_root
        repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent.parent
        git_script = repo_root / "usr" / "bin" / "biglinux-tts-speak"
        exec_path = (
            str(git_script) if git_script.exists() else "/usr/bin/biglinux-tts-speak"
        )

        content = f"""[Desktop Entry]
Type=Application
Exec={exec_path}
Icon=tts-biglinux
Categories=Utility;Accessibility;
StartupNotify=false
NoDisplay=true
        X-KDE-Shortcuts={kde_key}
Name=BigLinux TTS
GenericName=Speech or stop selected text
GenericName[pt_BR]=Narrador de texto (Alt+V)

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
        desktop_dst.parent.mkdir(parents=True, exist_ok=True)
        desktop_dst.write_text(content, encoding="utf-8")
        return desktop_dst

    @classmethod
    def update_khotkeys(cls, accel: str) -> None:
        """Update the KDE shortcut with the new keybinding in the background."""
        kde_shortcut = cls.gtk_accel_to_kde(accel)
        logger.info("Updating KDE shortcut to: %s", kde_shortcut)

        # 1. Update local .desktop file with the new X-KDE-Shortcuts
        cls.ensure_desktop_file(kde_shortcut)

        # 2. Radical Cleanup — Unregister zombies via DBus first
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
                        val = f"{kde_shortcut},{kde_shortcut},Speech or stop selected text"
                        subprocess.run(cmd + [val], timeout=2, check=False)
                    else:
                        subprocess.run(cmd + ["--delete"], timeout=2, check=False)
                except Exception:
                    pass

        # 3. Synchronize Legacy KHotKeys (.khotkeys file)
        import sys
        # Dynamic path detection (DesktopIntegrationService.py is in services/ subdir)
        # services/ -> tts-biglinux/ -> biglinux/ -> share/ -> usr/ -> (root)
        repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent.parent
        main_py = repo_root / "usr" / "share" / "biglinux" / "tts-biglinux" / "main.py"
        if main_py.exists():
            exec_path = f"{sys.executable} {main_py} --speak"
        else:
            exec_path = "/usr/bin/biglinux-tts-speak"
        
        cls.sync_khotkeys(kde_shortcut, exec_path)

        # 4. Rebuild system caches
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

    @staticmethod
    def ensure_icon_available() -> None:
        """Ensure the tts-biglinux icon is resolvable in user's icon theme."""
        system_icon = Path("/usr/share/icons/hicolor/scalable/apps/tts-biglinux.svg")
        local_icon_dir = (
            Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
        )
        local_icon = local_icon_dir / "tts-biglinux.svg"

        if local_icon.exists() or not system_icon.exists():
            return
        local_icon_dir.mkdir(parents=True, exist_ok=True)
        try:
            local_icon.symlink_to(system_icon)
            logger.info("Created icon symlink: %s -> %s", local_icon, system_icon)
        except OSError as e:
            logger.warning("Could not create icon symlink: %s", e)

    @classmethod
    def refresh_plasma_launcher(cls) -> bool:
        """Refresh Plasma launcher config without full restart. Returns True if successful."""
        cls.update_desktop_database()
        reloaded = False
        try:
            result = subprocess.run(
                [
                    "qdbus6",
                    "org.kde.plasmashell",
                    "/PlasmaShell",
                    "org.kde.PlasmaShell.evaluateScript",
                    "panels().forEach(p => p.reloadConfig())",
                ],
                timeout=5,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                reloaded = True
        except (OSError, subprocess.TimeoutExpired):
            pass

        if not reloaded:
            try:
                subprocess.run(
                    [
                        "dbus-send",
                        "--session",
                        "--type=signal",
                        "/org/kde/PlasmaShell",
                        "org.kde.PlasmaShell.configChanged",
                    ],
                    timeout=3,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        return reloaded

    @classmethod
    def toggle_launcher_pin(cls, active: bool, kde_key: str) -> bool:
        """Pin/unpin the application to KDE Plasma taskbar."""
        cls.ensure_icon_available()
        cls.ensure_desktop_file(kde_key)

        launcher_entry = "applications:biglinux-tts-speak.desktop"
        plasma_cfg = Path.home() / ".config" / "plasma-org.kde.plasma.desktop-appletsrc"

        if not plasma_cfg.exists():
            logger.warning("Plasma config not found: %s", plasma_cfg)
            return False

        try:
            lines = plasma_cfg.read_text().splitlines()
            changed = False
            for i, line in enumerate(lines):
                if not line.startswith("launchers="):
                    continue
                launchers = line.split("=", 1)[1]
                entries = [e.strip() for e in launchers.split(",") if e.strip()]

                if active and launcher_entry not in entries:
                    entries.append(launcher_entry)
                    lines[i] = "launchers=" + ",".join(entries)
                    changed = True
                elif not active and launcher_entry in entries:
                    entries.remove(launcher_entry)
                    lines[i] = "launchers=" + ",".join(entries)
                    changed = True

            if changed:
                plasma_cfg.write_text("\n".join(lines) + "\n")
                return cls.refresh_plasma_launcher()
        except OSError as e:
            logger.warning("Could not update Plasma launchers: %s", e)
        return False

    @staticmethod
    def _render_khotkeys_content(kde_shortcut: str, exec_path: str) -> str:
        """Helper to generate .khotkeys file content."""
        return f"""[Main]
ImportId=biglinux-tts
Version=2
Autostart=true
Disabled=false

[Data]
DataCount=1

[Data_1]
Comment=Global keyboard shortcut to speak selected text
Enabled=true
Name=BigLinux TTS Speak
Type=COMMAND_SHORTCUT_ACTION_DATA

[Data_1Actions]
ActionsCount=1

[Data_1Actions0]
Command={exec_path}
Type=COMMAND

[Data_1Conditions]
Comment=
ConditionsCount=0

[Data_1Triggers]
Comment=Simple_action
TriggersCount=1

[Data_1Triggers0]
Key={kde_shortcut}
Type=SHORTCUT
"""

    @classmethod
    def sync_khotkeys(cls, kde_shortcut: str, exec_path: str) -> None:
        """Update khotkeys configuration for backward compatibility.
        
        Writes to local user config and development file in the repository.
        Uses atomic writing to prevent 0-byte files on interruption.
        """
        import os
        import tempfile
        from pathlib import Path

        def _atomic_write(file_path: Path, content: str) -> bool:
            """Write content to file atomically."""
            try:
                # Only write if content is different
                if file_path.exists():
                    try:
                        if file_path.read_text(encoding="utf-8") == content:
                            return False
                    except Exception:
                        pass

                # Write to temp file first
                fd, temp_path = tempfile.mkstemp(dir=str(file_path.parent), text=True)
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        f.write(content)
                    
                    # Atomic rename
                    os.replace(temp_path, str(file_path))
                    # Ensure permissions are correct (0644)
                    os.chmod(str(file_path), 0o644)
                    return True
                except Exception as e:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                    raise e
            except Exception as e:
                logger.error("Atomic write failed for %s: %s", file_path, e)
                return False

        changed = False

        # 1. Update dev file if reachable (Always use production path for the repo)
        repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent.parent
        dev_khotkeys = repo_root / "usr" / "share" / "khotkeys" / "ttsbiglinux.khotkeys"
        
        if dev_khotkeys.exists() and os.access(dev_khotkeys, os.W_OK):
            # For the repository file, always use the standard production path
            production_content = cls._render_khotkeys_content(kde_shortcut, "/usr/bin/biglinux-tts-speak")
            logger.debug("KHotKeys Sync: dev file rewrite attempt. Content length: %d", len(production_content))
            if _atomic_write(dev_khotkeys, production_content):
                logger.info("Updated dev khotkeys to production path: %s", dev_khotkeys)
                changed = True

        # 2. Update user local khotkeys (use the dynamic exec_path for dev/testing)
        local_khotkeys_dir = Path.home() / ".local" / "share" / "khotkeys"
        local_khotkeys = local_khotkeys_dir / "tts-biglinux.khotkeys"
        try:
            local_khotkeys_dir.mkdir(parents=True, exist_ok=True)
            local_content = cls._render_khotkeys_content(kde_shortcut, exec_path)
            if _atomic_write(local_khotkeys, local_content):
                logger.debug("Updated local khotkeys (dynamic path): %s", local_khotkeys)
                changed = True
        except Exception as e:
            logger.debug("Failed to write local khotkeys: %s", e)

        # 3. Handle kded reread only if something actually changed
        if changed:
            DesktopIntegrationService._trigger_khotkeys_reload()

    @staticmethod
    def _trigger_khotkeys_reload() -> None:
        """Tell khotkeys to reload if it exists."""
        # Check if khotkeys module is loaded in kded (5 or 6)
        modules = []
        for kded in ["org.kde.kded6", "org.kde.kded5"]:
            try:
                res = subprocess.run(
                    ["qdbus", kded, "/kded", "org.kde.kded6.loadedModules"],
                    capture_output=True, text=True, timeout=2
                )
                if "khotkeys" in res.stdout:
                    modules.append(kded)
            except:
                pass
        
        for kded in modules:
            try:
                subprocess.run(
                    ["dbus-send", "--session", "--type=method_call",
                     f"--dest={kded}", "/modules/khotkeys", 
                     "org.kde.khotkeys.reread_configuration"],
                    timeout=2, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except:
                pass
