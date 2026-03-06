"""
Service to handle desktop integration logic, such as KDE shortcuts and launcher pins.

Separates OS/DE specific DBus/X11/Wayland behavior from the UI layer.
"""

from __future__ import annotations

import logging
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
        comp = "biglinux-tts-speak.desktop"
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
        """Ensure biglinux-tts-speak.desktop exists locally with current shortcut."""
        local_apps = Path.home() / ".local" / "share" / "applications"
        desktop_dst = local_apps / "biglinux-tts-speak.desktop"

        # Dynamic path detection for the executable script
        repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent
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

        # Groups to clean and register in (Plasma 5 and 6)
        groups = [
            ("services", "biglinux-tts-speak.desktop"),
            ("", "biglinux-tts-speak.desktop"),
            ("services", "br.com.biglinux.tts.desktop"),
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

        # 3. Rebuild system caches
        cls.update_desktop_database()

        # 4. Force kglobalaccel to re-read
        cls.reload_kglobalaccel()

        # 5. Injection layer
        cls.unregister_shortcut_from_memory()
        time.sleep(0.15)

        from application import TTSApplication

        TTSApplication._inject_shortcut_dbus_static(kde_shortcut)

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
