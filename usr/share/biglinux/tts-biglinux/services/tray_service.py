"""
System tray icon via Qt6 subprocess.

Runs a minimal PySide6 QSystemTrayIcon in a separate process to avoid
GTK3/GTK4 conflicts. Communicates via stdin/stdout lines.

While reading, the tray icon breathes (opacity only, constant size — see
tray_icon_frames.py) and the tooltip shows the
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
    from PySide6.QtGui import QIcon, QCursor
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
except ImportError:
    send({"event": "error", "message": "PySide6 not installed (python-pyside6). Tray icon is disabled."})
    sys.exit(1)


try:
    title       = sys.argv[1] if len(sys.argv) > 1 else "App"
    tooltip     = sys.argv[2] if len(sys.argv) > 2 else title
    icon_dark   = sys.argv[3] if len(sys.argv) > 3 else ""
    icon_light  = sys.argv[4] if len(sys.argv) > 4 else ""
    frames_py   = sys.argv[5] if len(sys.argv) > 5 else ""

    # Breathing frames live in services/tray_icon_frames.py (loaded by path:
    # this helper runs outside the app's import path).
    import importlib.util
    _spec = importlib.util.spec_from_file_location("tray_icon_frames", frames_py)
    tif = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(tif)

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

    # Breathing while reading: opacity only, never size (see tray_icon_frames:
    # every frame keeps the original pixel size AND devicePixelRatio; the old
    # code dropped the ratio and the icon shrank on scaled screens).
    def screen_dprs():
        return [1.0, app.devicePixelRatio()] + [s.devicePixelRatio() for s in app.screens()]

    frames = {"set": tif.FrameSet(get_icon_for_theme(), screen_dprs()), "level": None}
    breather = tif.Breather()

    tray = QSystemTrayIcon(frames["set"].full(), app)
    tray.setToolTip(tooltip)

    def show_level(level) -> None:
        if level != frames["level"]:  # unchanged frame: nothing sent to the panel
            frames["level"] = level
            tray.setIcon(frames["set"].frame(level))

    def anim_tick() -> None:
        opacity, running = breather.tick(tif.TICK_MS)
        show_level(tif.level_for(opacity))
        if not running:
            anim_timer.stop()

    anim_timer = QTimer()
    anim_timer.setInterval(tif.TICK_MS)
    anim_timer.timeout.connect(anim_tick)

    def render_icon() -> None:
        # Drive the breathing from the speaking/paused state.
        if state["speaking"] and not state["paused"]:
            if not state.get("animate", True):
                breather.hold(tif.STEADY_OPACITY)  # reduced motion: no breathing
            elif breather.mode != "breathing":
                breather.start()
        elif state["speaking"] and state["paused"]:
            breather.pause()
        else:
            breather.stop()
        if breather.running and not anim_timer.isActive():
            anim_timer.start()
        elif not breather.running:
            anim_timer.stop()
            show_level(tif.level_for(breather.opacity))

    def refresh_base() -> None:
        frames["set"] = tif.FrameSet(get_icon_for_theme(), screen_dprs())
        frames["level"] = None
        show_level(tif.level_for(breather.opacity))

    app.screenAdded.connect(lambda *_: refresh_base())
    app.screenRemoved.connect(lambda *_: refresh_base())

    theme = {"dark": is_dark_theme()}

    def on_theme_changed(*_) -> None:
        refresh_base()

    def check_theme() -> None:
        # Polled: only repaint when light/dark actually flipped. Re-setting the
        # icon every poll would spam the panel with NewIcon over D-Bus.
        dark = is_dark_theme()
        if dark != theme["dark"]:
            theme["dark"] = dark
            on_theme_changed()

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
                # New idle tooltip (e.g. the shortcut changed).
                state["tooltip"] = msg.get("text", "") or tooltip
                if not state["speaking"]:
                    tray.setToolTip(state["tooltip"])
            elif cmd == "set_speaking":
                state["speaking"] = bool(msg.get("speaking", False))
                state["paused"] = bool(msg.get("paused", False))
                state["animate"] = bool(msg.get("animate", True))
                label = msg.get("label", "")
                idle_tip = state.get("tooltip") or tooltip
                tray.setToolTip(label or idle_tip if state["speaking"] else idle_tip)
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
    theme_timer.timeout.connect(check_theme)
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
    ) -> None:
        self._title = title
        self._tooltip = tooltip or title
        self._icon_dark_path = icon_dark_path
        self._icon_light_path = icon_light_path
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
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "tray_icon_frames.py"),
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

    def set_tooltip(self, text: str) -> None:
        """Tooltip shown while idle (playback labels take over while reading)."""
        self._tooltip = text
        self._send({"cmd": "set_tooltip", "text": text})

    def set_speaking(
        self,
        speaking: bool,
        label: str = "",
        *,
        paused: bool = False,
        animate: bool = True,
    ) -> None:
        """Drive the tray icon animation and tooltip for the playback state.

        While ``speaking`` and not ``paused`` the icon breathes (opacity only) and
        the tooltip shows ``label``; while ``paused`` the icon holds a steady dim.
        ``animate`` False (the desktop asks for reduced motion) holds a steady
        level instead of breathing.
        """
        msg: dict = {"cmd": "set_speaking", "speaking": speaking, "paused": paused, "animate": animate}
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
