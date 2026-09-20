"""
System tray icon via Qt6 subprocess.

Runs a minimal PySide6 QSystemTrayIcon in a separate process to avoid
GTK3/GTK4 conflicts. Communicates via stdin/stdout lines.

While reading, the tray icon pulses (fade in/out) and the tooltip shows the
playing label; a left-click stops playback (and otherwise reads the selection).
The right-click context menu is STATIC: the parent always sends the full,
fixed-order list of rows (idle "Read text" plus the playback controls) and the
helper creates them once, then only toggles each row's visible/label/enabled.
Plasma renders the menu through DBusMenu, where property updates propagate
reliably but structural rebuilds do not (see the helper's comment). The menu is
compositor-anchored to the tray icon, so it works on Wayland where a free popup
would not.

Protocol (parent → child): JSON lines
  {"cmd": "quit"}
  {"cmd": "set_menu", "items": [{"id":1,"label":"X","visible":true},
       {"id":10,"label":"Playing…","enabled":false,"visible":false},
       {"id":3,"separator":true,"visible":false}]}
  {"cmd": "set_tooltip", "text": "..."}
  {"cmd": "set_speaking", "speaking": true, "paused": false, "label": "..."}
  {"cmd": "update_icon"}

Protocol (child → parent): JSON lines
  {"event": "activate"}                       # left-click while idle → read selection
  {"event": "menu", "id": 1}                  # menu item (or submenu item) clicked
  {"event": "player", "action": "stop"}       # left-click while speaking → stop
  {"event": "ready"}                          # tray icon visible
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import textwrap
from typing import Callable

from gi.repository import GLib

logger = logging.getLogger(__name__)

_HELPER_SCRIPT = textwrap.dedent("""
import json
import math
import os
import signal
import sys

def send(data: dict) -> None:
    try:
        sys.stdout.write(json.dumps(data) + "\\n")
        sys.stdout.flush()
    except Exception:
        pass

try:
    from PySide6.QtCore import Qt, QTimer, QSize
    from PySide6.QtGui import QIcon, QCursor, QPixmap, QPainter
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
except ImportError:
    send({"event": "error", "message": "PySide6 not installed (python-pyside6). Tray icon is disabled."})
    sys.exit(1)


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

    state = {"speaking": False, "paused": False}

    def is_dark_theme() -> bool:
        return app.palette().window().color().lightness() < 128

    def get_icon_for_theme() -> QIcon:
        path = icon_dark if is_dark_theme() else icon_light
        if path:
            return QIcon(path)
        return QIcon.fromTheme("tts-biglinux-symbolic")

    # Base icon + a 64px pixmap we repaint at varying opacity to pulse the tray
    # icon while reading. Both refresh on palette (theme) changes.
    base = {"icon": get_icon_for_theme()}
    base["pixmap"] = base["icon"].pixmap(QSize(64, 64))

    tray = QSystemTrayIcon(base["icon"], app)
    tray.setToolTip(tooltip)

    def apply_opacity(op) -> None:
        pm = base["pixmap"]
        if pm is None or pm.isNull():
            tray.setIcon(base["icon"])
            return
        out = QPixmap(pm.size())
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setOpacity(max(0.0, min(1.0, op)))
        p.drawPixmap(0, 0, pm)
        p.end()
        tray.setIcon(QIcon(out))

    def refresh_base() -> None:
        base["icon"] = get_icon_for_theme()
        base["pixmap"] = base["icon"].pixmap(QSize(64, 64))

    # Fade in/out pulse while reading (not while paused).
    pulse = {"phase": 0.0}

    def pulse_tick() -> None:
        pulse["phase"] += 0.30
        op = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(pulse["phase"]))
        apply_opacity(op)

    pulse_timer = QTimer()
    pulse_timer.setInterval(80)
    pulse_timer.timeout.connect(pulse_tick)

    def render_icon() -> None:
        # Repaint the tray icon for the current speaking/paused state.
        if state["speaking"] and not state["paused"]:
            if not pulse_timer.isActive():
                pulse["phase"] = 0.0
                pulse_timer.start()
        elif state["speaking"] and state["paused"]:
            pulse_timer.stop()
            apply_opacity(0.45)   # steady dim = paused
        else:
            pulse_timer.stop()
            tray.setIcon(base["icon"])

    def on_theme_changed(*_) -> None:
        refresh_base()
        render_icon()

    app.paletteChanged.connect(on_theme_changed)

    # Static context menu. Plasma renders SNI menus through DBusMenu, and Qt's
    # exporter does not reliably propagate STRUCTURAL changes (removing/adding
    # rows) made while the menu is closed: the panel keeps a stale layout, and a
    # rebuild triggered on aboutToShow races Plasma's GetLayout ("one revision
    # behind"). Item PROPERTY changes, however, propagate immediately as
    # ItemsPropertiesUpdated and the importer applies them to the existing rows.
    # So every possible row is created once, in a fixed order, and playback state
    # only toggles each row's visibility / label / enabled flag.
    menu = QMenu()
    tray.setContextMenu(menu)
    actions = {}   # item id -> QAction (separators included)
    order = []     # item ids in creation order

    def on_menu_click(item_id) -> None:
        send({"event": "menu", "id": item_id})

    def build_actions(items) -> None:
        menu.clear()
        actions.clear()
        order.clear()
        for item in items:
            iid = item["id"]
            if item.get("separator"):
                a = menu.addSeparator()
            else:
                a = menu.addAction(item.get("label", ""))
                a.triggered.connect(lambda checked, i=iid: on_menu_click(i))
            actions[iid] = a
            order.append(iid)

    def apply_props(items) -> None:
        for item in items:
            a = actions[item["id"]]
            if not item.get("separator"):
                a.setText(item.get("label", ""))
                a.setEnabled(bool(item.get("enabled", True)))
            a.setVisible(bool(item.get("visible", True)))

    def set_menu(items) -> None:
        # Structure is created on the first call (or if the parent ever changes
        # the set of rows); afterwards only properties are updated.
        if [it["id"] for it in items] != order:
            build_actions(items)
        apply_props(items)

    def on_activated(reason) -> None:
        # Left-click: while reading it stops playback; otherwise it reads the
        # current selection. Middle-click pops the native context menu (its
        # activation carries a valid input serial that Wayland requires).
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if state["speaking"]:
                send({"event": "player", "action": "stop"})
            else:
                send({"event": "activate"})
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            menu.popup(QCursor.pos())

    # Read the parent's commands from the raw, non-blocking fd into our own
    # buffer. NEVER mix select() with sys.stdin.readline(): the parent sends
    # bursts of several lines (set_speaking + set_menu), readline() slurps them
    # all into TextIOWrapper's buffer, select() then reports the fd idle, and
    # the remaining lines sit unprocessed until the NEXT message — which made
    # the menu lag exactly one update behind.
    stdin_fd = sys.stdin.fileno()
    os.set_blocking(stdin_fd, False)
    inbuf = bytearray()

    def read_lines():
        while True:
            try:
                chunk = os.read(stdin_fd, 65536)
            except BlockingIOError:
                break
            if not chunk:          # EOF: parent is gone
                app.quit()
                return
            inbuf.extend(chunk)
            if len(chunk) < 65536:
                break
        while True:
            nl = inbuf.find(b"\\n")
            if nl < 0:
                break
            line = bytes(inbuf[:nl]); del inbuf[:nl + 1]
            yield line.decode("utf-8", "replace").strip()

    def handle_input() -> None:
        for line in read_lines():
            if not line:
                continue
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            cmd = msg.get("cmd")
            if cmd == "quit":
                app.quit()
            elif cmd == "set_menu":
                set_menu(msg.get("items", []))
            elif cmd == "set_tooltip":
                tray.setToolTip(msg.get("text", ""))
            elif cmd == "set_speaking":
                state["speaking"] = bool(msg.get("speaking", False))
                state["paused"] = bool(msg.get("paused", False))
                label = msg.get("label", "")
                tray.setToolTip(label or tooltip if state["speaking"] else tooltip)
                render_icon()
            elif cmd == "update_icon":
                on_theme_changed()

    tray.activated.connect(on_activated)
    tray.show()
    send({"event": "ready"})

    timer = QTimer()
    timer.timeout.connect(handle_input)
    timer.start(100)

    theme_timer = QTimer()
    theme_timer.timeout.connect(on_theme_changed)
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
    """Simple menu item descriptor.

    A ``submenu`` (list of child MenuItems) turns this into a parent row whose
    children open in a nested menu; the parent itself has no direct action.
    """

    def __init__(
        self,
        item_id: int,
        label: str,
        callback: Callable[[], None] | None = None,
        *,
        separator: bool = False,
        submenu: list["MenuItem"] | None = None,
        enabled: bool = True,
        visible: bool = True,
    ) -> None:
        self.item_id = item_id
        self.label = label
        self.callback = callback
        self.separator = separator
        self.submenu = submenu
        self.enabled = enabled
        # Rows are created once in the helper; state changes only toggle this.
        self.visible = visible


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
        # Every row (separators included) carries a stable id so the helper can
        # create the structure once and then only update properties.
        items = []
        for m in self._menu_items:
            entry: dict = {"id": m.item_id, "visible": m.visible}
            if m.separator:
                entry["separator"] = True
            else:
                entry["label"] = m.label
                entry["enabled"] = m.enabled
            items.append(entry)
        self._send({"cmd": "set_menu", "items": items})

    def _find_callback(self, item_id: int) -> Callable[[], None] | None:
        """Find a menu item's callback, searching submenus too."""
        for m in self._menu_items:
            if m.item_id == item_id and m.callback:
                return m.callback
            for c in (m.submenu or []):
                if c.item_id == item_id and c.callback:
                    return c.callback
        return None

    def set_speaking(
        self,
        speaking: bool,
        label: str = "",
        *,
        paused: bool = False,
    ) -> None:
        """Drive the tray icon animation and tooltip for the playback state.

        While ``speaking`` and not ``paused`` the icon pulses (fade in/out) and
        the tooltip shows ``label``; while ``paused`` the icon holds a steady dim.
        """
        msg: dict = {"cmd": "set_speaking", "speaking": speaking, "paused": paused}
        if label:
            msg["label"] = label
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
                    cb = self._find_callback(msg.get("id"))
                    if cb:
                        cb()
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
