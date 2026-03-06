"""
System tray icon via Qt6 subprocess.

Runs a minimal PySide6 QSystemTrayIcon in a separate process to avoid
GTK3/GTK4 conflicts. Communicates via stdin/stdout lines.

Protocol (parent → child): JSON lines
  {"cmd": "quit"}
  {"cmd": "set_menu", "items": [{"id":1,"label":"X"}, {"id":2,"separator":true}]}
  {"cmd": "set_tooltip", "text": "..."}

Protocol (child → parent): JSON lines
  {"event": "activate"}          # left-click
  {"event": "menu", "id": 1}     # menu item clicked
  {"event": "ready"}             # tray icon visible
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import textwrap
from typing import Callable

from gi.repository import GLib

logger = logging.getLogger(__name__)

_HELPER_SCRIPT = textwrap.dedent("""\
import json
import signal
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

icon_name = sys.argv[1] if len(sys.argv) > 1 else "application-x-executable"
title = sys.argv[2] if len(sys.argv) > 2 else "App"
tooltip = sys.argv[3] if len(sys.argv) > 3 else title
icon_path = sys.argv[4] if len(sys.argv) > 4 else ""

sys.argv[0] = title
app = QApplication(sys.argv)
app.setApplicationName(title)
app.setDesktopFileName("br.com.biglinux.tts")
app.setQuitOnLastWindowClosed(False)

# Prefer direct file path (Qt strips -symbolic suffix)
if icon_path:
    icon = QIcon(icon_path)
else:
    icon = QIcon.fromTheme(icon_name)

tray = QSystemTrayIcon(icon, app)
tray.setToolTip(tooltip)

menu = QMenu()
tray.setContextMenu(menu)

action_map: dict[int, QAction] = {}


def send(data: dict) -> None:
    sys.stdout.write(json.dumps(data) + "\\n")
    sys.stdout.flush()


def on_activated(reason: QSystemTrayIcon.ActivationReason) -> None:
    if reason == QSystemTrayIcon.ActivationReason.Trigger:
        send({"event": "activate"})


def on_menu_click(item_id: int) -> None:
    send({"event": "menu", "id": item_id})


def handle_input() -> None:
    import select
    while select.select([sys.stdin], [], [], 0)[0]:
        line = sys.stdin.readline()
        if not line:
            app.quit()
            return
        try:
            msg = json.loads(line.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        cmd = msg.get("cmd")
        if cmd == "quit":
            app.quit()
        elif cmd == "set_menu":
            menu.clear()
            action_map.clear()
            for item in msg.get("items", []):
                if item.get("separator"):
                    menu.addSeparator()
                else:
                    item_id = item["id"]
                    action = menu.addAction(item["label"])
                    action.triggered.connect(lambda checked, iid=item_id: on_menu_click(iid))
                    action_map[item_id] = action
        elif cmd == "set_tooltip":
            tray.setToolTip(msg.get("text", ""))


tray.activated.connect(on_activated)
tray.show()
send({"event": "ready"})

# Poll stdin periodically
from PySide6.QtCore import QTimer
timer = QTimer()
timer.timeout.connect(handle_input)
timer.start(100)

signal.signal(signal.SIGTERM, lambda *_: app.quit())
signal.signal(signal.SIGINT, lambda *_: app.quit())

sys.exit(app.exec())
""")


class MenuItem:
    """Simple menu item descriptor."""

    def __init__(
        self,
        item_id: int,
        label: str,
        callback: Callable[[], None] | None = None,
        *,
        separator: bool = False,
    ) -> None:
        self.item_id = item_id
        self.label = label
        self.callback = callback
        self.separator = separator


class TrayIcon:
    """System tray icon using a Qt6 subprocess for native Plasma support."""

    def __init__(
        self,
        icon_name: str = "tts-biglinux-symbolic",
        title: str = "BigLinux TTS",
        tooltip: str = "",
        icon_path: str = "",
    ) -> None:
        self._icon_name = icon_name
        self._title = title
        self._tooltip = tooltip or title
        self._icon_path = icon_path
        self._proc: subprocess.Popen | None = None
        self._menu_items: list[MenuItem] = []
        self._io_watch_id: int = 0

        # Callbacks
        self.on_activate: Callable[[], None] | None = None

    def set_menu(self, items: list[MenuItem]) -> None:
        """Set the context menu items."""
        self._menu_items = items
        self._send_menu()

    def register(self) -> None:
        """Start the Qt6 tray helper subprocess."""
        cmd = [
            sys.executable,
            "-c",
            _HELPER_SCRIPT,
            self._icon_name,
            self._title,
            self._tooltip,
        ]
        if self._icon_path:
            cmd.append(self._icon_path)
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        # Watch stdout for events using GLib IO
        if self._proc.stdout:
            fd = self._proc.stdout.fileno()
            os.set_blocking(fd, False)
            channel = GLib.IOChannel.unix_new(fd)
            channel.set_encoding(None)
            self._io_watch_id = GLib.io_add_watch(
                channel,
                GLib.PRIORITY_DEFAULT,
                GLib.IOCondition.IN | GLib.IOCondition.HUP,
                self._on_child_output,
            )
        logger.debug("Tray helper subprocess started (pid=%d)", self._proc.pid)

    def unregister(self) -> None:
        """Stop the helper subprocess."""
        if self._io_watch_id:
            GLib.source_remove(self._io_watch_id)
            self._io_watch_id = 0
        if self._proc:
            self._send({"cmd": "quit"})
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        logger.debug("Tray helper subprocess stopped")

    def _send(self, msg: dict) -> None:
        """Send a JSON message to the helper."""
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(json.dumps(msg) + "\n")
                self._proc.stdin.flush()
            except (OSError, BrokenPipeError):
                logger.warning("Failed to send to tray helper")

    def _send_menu(self) -> None:
        """Send current menu items to the helper."""
        items = []
        for m in self._menu_items:
            if m.separator:
                items.append({"separator": True})
            else:
                items.append({"id": m.item_id, "label": m.label})
        self._send({"cmd": "set_menu", "items": items})

    def _on_child_output(
        self, channel: GLib.IOChannel, condition: GLib.IOCondition
    ) -> bool:
        """Handle output from the helper subprocess."""
        if condition & GLib.IOCondition.HUP:
            logger.warning("Tray helper subprocess ended")
            return False

        try:
            while True:
                status, line, _length, _term = channel.read_line()
                if status != GLib.IOStatus.NORMAL or not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                event = msg.get("event")
                if event == "activate":
                    if self.on_activate:
                        self.on_activate()
                elif event == "menu":
                    item_id = msg.get("id")
                    for m in self._menu_items:
                        if m.item_id == item_id and m.callback:
                            m.callback()
                            break
                elif event == "ready":
                    logger.info("Tray icon is visible")
                    self._send_menu()
        except Exception as e:
            logger.warning("Error reading tray helper: %s", e)

        return True
