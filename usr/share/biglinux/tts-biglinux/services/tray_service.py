"""
System tray icon via Qt6 subprocess.

Runs a minimal PySide6 QSystemTrayIcon in a separate process to avoid
GTK3/GTK4 conflicts. Communicates via stdin/stdout lines.

The context menu embeds a mini player (title + play/stop + progress bar) at the
top via a QWidgetAction; it is shown only while reading. The player pops out of
the tray icon with the native right-click menu (compositor-anchored, so it works
on Wayland where a free-floating popup would not).

Protocol (parent → child): JSON lines
  {"cmd": "quit"}
  {"cmd": "set_menu", "items": [{"id":1,"label":"X"}, {"id":2,"separator":true}]}
  {"cmd": "set_tooltip", "text": "..."}
  {"cmd": "set_speaking", "speaking": true, "label": "...",
       "duration_ms": 3660, "show_player": true}   # drives the mini player
  {"cmd": "update_icon"}

Protocol (child → parent): JSON lines
  {"event": "activate"}                       # left-click → read selection
  {"event": "menu", "id": 1}                  # menu item clicked
  {"event": "player", "action": "stop"}       # player stop/play clicked
  {"event": "ready"}                          # tray icon visible
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Callable

from gi.repository import GLib

logger = logging.getLogger(__name__)

_HELPER_SCRIPT = textwrap.dedent("""
import json
import select
import signal
import sys
import time

def send(data: dict) -> None:
    try:
        sys.stdout.write(json.dumps(data) + "\\n")
        sys.stdout.flush()
    except Exception:
        pass

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QIcon, QCursor
    from PySide6.QtWidgets import (
        QApplication, QMenu, QSystemTrayIcon, QWidget, QWidgetAction,
        QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QProgressBar,
    )
except ImportError:
    send({"event": "error", "message": "PySide6 not installed (python-pyside6). Tray icon is disabled."})
    sys.exit(1)


def _fmt(ms) -> str:
    s = max(0, int(ms / 1000))
    return "%d:%02d" % (s // 60, s % 60)


class MiniPlayer(QWidget):
    '''Mini player embedded at the top of the tray context menu: title being
    read + play/stop buttons + elapsed/total time + progress bar. Rendered by
    the compositor anchored to the tray icon, so it "pops out of the systray".'''

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("player")
        self.setFixedWidth(300)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        self.title = QLabel("BigLinux TTS")
        self.title.setObjectName("title")
        self.title.setWordWrap(True)
        self.title.setMaximumHeight(42)
        lay.addWidget(self.title)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.play_btn = QToolButton()
        self.play_btn.setText("\\u25B6")
        self.play_btn.setToolTip("Play")
        self.play_btn.clicked.connect(lambda: send({"event": "player", "action": "play"}))
        self.stop_btn = QToolButton()
        self.stop_btn.setText("\\u25A0")
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.clicked.connect(lambda: send({"event": "player", "action": "stop"}))
        row.addWidget(self.play_btn)
        row.addWidget(self.stop_btn)

        self.elapsed = QLabel("0:00")
        self.elapsed.setObjectName("time")
        row.addWidget(self.elapsed)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        row.addWidget(self.bar, 1)
        self.total = QLabel("0:00")
        self.total.setObjectName("time")
        row.addWidget(self.total)
        lay.addLayout(row)

        self.setStyleSheet('''
          #player { background: #2b2b31; border-radius: 10px; }
          #title { color: #f2f2f2; font-weight: bold; }
          #time { color: rgba(255,255,255,0.70); font-size: 11px; }
          QProgressBar { background: rgba(255,255,255,0.15); border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }
          QProgressBar::chunk { background: #3584e4; border-radius: 3px; }
          QToolButton { color: #fff; background: rgba(255,255,255,0.12); border: none; border-radius: 15px; min-width: 30px; min-height: 30px; font-size: 13px; }
          QToolButton:hover { background: rgba(255,255,255,0.22); }
        ''')

        self._dur = 0
        self._t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)

    def start(self, title, dur_ms) -> None:
        self.title.setText(title or "BigLinux TTS")
        self._dur = max(0, int(dur_ms))
        self._t0 = time.monotonic()
        self.total.setText(_fmt(self._dur))
        self.elapsed.setText("0:00")
        self.bar.setValue(0)
        self._timer.start()

    def stop_ui(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        el = (time.monotonic() - self._t0) * 1000
        self.elapsed.setText(_fmt(el))
        if self._dur > 0:
            self.bar.setValue(int(min(1.0, el / self._dur) * 1000))


try:
    title       = sys.argv[1] if len(sys.argv) > 1 else "App"
    tooltip     = sys.argv[2] if len(sys.argv) > 2 else title
    icon_dark   = sys.argv[3] if len(sys.argv) > 3 else ""
    icon_light  = sys.argv[4] if len(sys.argv) > 4 else ""

    sys.argv[0] = title
    app = QApplication(sys.argv)
    app.setApplicationName(title)
    app.setDesktopFileName("br.com.biglinux.tts")
    app.setQuitOnLastWindowClosed(False)

    def is_dark_theme() -> bool:
        return app.palette().window().color().lightness() < 128

    def get_icon_for_theme():
        path = icon_dark if is_dark_theme() else icon_light
        if path:
            return QIcon(path)
        return QIcon.fromTheme("tts-biglinux-symbolic")

    tray = QSystemTrayIcon(get_icon_for_theme(), app)
    tray.setToolTip(tooltip)

    def update_icon_from_theme():
        tray.setIcon(get_icon_for_theme())

    app.paletteChanged.connect(lambda _: update_icon_from_theme())

    menu = QMenu()
    tray.setContextMenu(menu)

    # Mini player embedded at the top of the tray menu (parented to app so
    # rebuilding the item actions never deletes it).
    player = MiniPlayer()
    player_action = QWidgetAction(app)
    player_action.setDefaultWidget(player)
    menu.addAction(player_action)
    player_sep = menu.addSeparator()
    player_action.setVisible(False)
    player_sep.setVisible(False)

    item_actions = []

    def set_player_visible(vis) -> None:
        player_action.setVisible(vis)
        player_sep.setVisible(vis)

    def on_activated(reason) -> None:
        # Left-click = quick "read selection"; right-click shows the native
        # context menu (with the mini player) anchored to the tray icon.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            send({"event": "activate"})
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            # Middle-click pops the menu; the activation carries a valid input
            # serial so the compositor accepts the popup.
            menu.popup(QCursor.pos())

    def on_menu_click(item_id) -> None:
        send({"event": "menu", "id": item_id})

    def handle_input() -> None:
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
                for a in item_actions:
                    menu.removeAction(a)
                item_actions.clear()
                for item in msg.get("items", []):
                    if item.get("separator"):
                        a = menu.addSeparator()
                    else:
                        item_id = item["id"]
                        a = menu.addAction(item["label"])
                        a.triggered.connect(lambda checked, iid=item_id: on_menu_click(iid))
                    item_actions.append(a)
            elif cmd == "set_tooltip":
                tray.setToolTip(msg.get("text", ""))
            elif cmd == "set_speaking":
                speaking = msg.get("speaking", False)
                label = msg.get("label", "")
                if speaking and msg.get("show_player", True):
                    tray.setToolTip(label or tooltip)
                    player.start(label, msg.get("duration_ms", 0))
                    set_player_visible(True)
                else:
                    player.stop_ui()
                    set_player_visible(False)
                    if not speaking:
                        tray.setToolTip(tooltip)
            elif cmd == "update_icon":
                update_icon_from_theme()

    tray.activated.connect(on_activated)
    tray.show()
    send({"event": "ready"})

    timer = QTimer()
    timer.timeout.connect(handle_input)
    timer.start(100)

    theme_timer = QTimer()
    theme_timer.timeout.connect(update_icon_from_theme)
    theme_timer.start(2000)

    def _cleanup(*_):
        app.quit()

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    sys.exit(app.exec())
except Exception as e:
    send({"event": "error", "message": f"Tray crashed: {e}"})
    sys.exit(1)
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
    """System tray icon using a Qt6 subprocess for native Plasma support.

    Automatically switches between a white icon (dark themes) and a dark icon
    (light themes) by checking palette luminance in the helper process.
    """

    def __init__(
        self,
        title: str = "BigLinux TTS",
        tooltip: str = "",
        icon_dark_path: str = "",   # white icon – shown on dark backgrounds
        icon_light_path: str = "",  # dark icon  – shown on light backgrounds
        *,
        icon_name: str = "",        # compat: ignored (theme name for GTK)
        icon_path: str = "",        # compat: used as both dark/light fallback
    ) -> None:
        self._title = title
        self._tooltip = tooltip or title
        # Support legacy icon_path kwarg as fallback for both paths
        self._icon_dark_path = icon_dark_path or icon_path
        self._icon_light_path = icon_light_path or icon_path
        self._proc: subprocess.Popen | None = None
        self._menu_items: list[MenuItem] = []
        self._io_watch_id: int = 0

        # Callbacks
        self.on_activate: Callable[[], None] | None = None
        # on_player(action) where action is "play" or "stop"
        self.on_player: Callable[[str], None] | None = None

    def set_menu(self, items: list[MenuItem]) -> None:
        """Set the context menu items."""
        self._menu_items = items
        self._send_menu()

    def register(self) -> None:
        """Start the Qt6 tray helper subprocess."""
        cmd = [
            "/usr/bin/python3",
            "-c",
            _HELPER_SCRIPT,
            self._title,
            self._tooltip,
            self._icon_dark_path,
            self._icon_light_path,
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
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
            if self._proc.poll() is None:
                self._send({"cmd": "quit"})

            if self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except Exception:
                    pass

            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None
        logger.debug("Tray helper subprocess stopped")

    def _send(self, msg: dict) -> None:
        """Send a JSON message to the helper."""
        if self._proc and self._proc.stdin and not self._proc.stdin.closed:
            try:
                self._proc.stdin.write(json.dumps(msg) + "\n")
                self._proc.stdin.flush()
            except (OSError, BrokenPipeError, ValueError):
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

    def set_speaking(
        self,
        speaking: bool,
        label: str = "",
        *,
        duration_ms: int = 0,
        show_player: bool = True,
    ) -> None:
        """Update the tray tooltip and the pop-out mini player while speaking.

        When ``speaking`` is True and ``show_player`` is set, the helper pops a
        mini player out of the tray icon with play/stop controls and a progress
        bar that fills over ``duration_ms``.
        """
        msg: dict = {"cmd": "set_speaking", "speaking": speaking}
        if label:
            msg["label"] = label
        if speaking:
            msg["duration_ms"] = max(0, int(duration_ms))
            msg["show_player"] = bool(show_player)
        self._send(msg)

    def _on_child_output(
        self, channel: GLib.IOChannel, condition: GLib.IOCondition
    ) -> bool:
        """Handle output from the helper subprocess."""
        if condition & GLib.IOCondition.HUP:
            logger.warning("Tray helper subprocess ended")
            self._io_watch_id = 0
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
                    logger.warning("Tray helper stderr: %s", line)
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
                elif event == "player":
                    if self.on_player:
                        self.on_player(msg.get("action", ""))
                elif event == "ready":
                    logger.info("Tray icon is visible")
                    self._send_menu()
                elif event == "error":
                    logger.error("Tray helper error: %s", msg.get("message"))
                elif event == "debug":
                    logger.debug("Tray helper: %s", msg.get('message'))
        except Exception as e:
            logger.warning("Error reading tray helper: %s", e)

        return True
