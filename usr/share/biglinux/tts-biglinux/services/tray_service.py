"""
System tray icon via Qt6 subprocess.

Runs a minimal PySide6 QSystemTrayIcon in a separate process to avoid
GTK3/GTK4 conflicts. Communicates via stdin/stdout lines.

Protocol (parent → child): JSON lines
  {"cmd": "quit"}
  {"cmd": "set_menu", "items": [{"id":1,"label":"X"}, {"id":2,"separator":true}]}
  {"cmd": "set_tooltip", "text": "..."}
  {"cmd": "set_icon", "path": "/path/to/icon.svg"}

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
from pathlib import Path
from typing import Callable

from gi.repository import GLib

logger = logging.getLogger(__name__)

_HELPER_SCRIPT = textwrap.dedent("""
import json
import math
import os
import random
import signal
import struct
import subprocess
import sys
import threading

def send(data: dict) -> None:
    try:
        sys.stdout.write(json.dumps(data) + "\\n")
        sys.stdout.flush()
    except Exception:
        pass

try:
    from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QSize, Signal, QObject
    from PySide6.QtGui import (
        QAction, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
    )
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget
except ImportError:
    send({"event": "error", "message": "PySide6 not installed (python-pyside6). Tray icon is disabled."})
    sys.exit(1)


class AudioLevelSignal(QObject):
    '''Thread-safe bridge: audio thread emits levels → UI thread receives.'''
    levels_ready = Signal(list)


class AudioMonitor:
    '''Captures audio peaks from PulseAudio/PipeWire default sink monitor.

    Reads raw s16le 1ch 16kHz from parec, splits into NUM_BANDS frequency-ish
    buckets by sub-dividing each read chunk, and emits RMS per bucket.
    '''
    NUM_BANDS = 7

    def __init__(self, signal_bridge: AudioLevelSignal) -> None:
        self._signal = signal_bridge
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None

    def _read_loop(self) -> None:
        try:
            # Record from default sink monitor, mono 16-bit 16 kHz
            self._proc = subprocess.Popen(
                [
                    "parec",
                    "--format=s16le",
                    "--channels=1",
                    "--rate=16000",
                    "--device=@DEFAULT_MONITOR@",
                    "--latency-msec=50",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self._running = False
            return

        CHUNK = 1024  # 512 samples (16-bit) → ~32ms at 16kHz
        while self._running and self._proc and self._proc.poll() is None:
            data = self._proc.stdout.read(CHUNK)
            if not data:
                break
            samples = struct.unpack(f"<{len(data)//2}h", data)
            bands = self._compute_bands(samples)
            self._signal.levels_ready.emit(bands)

        self.stop()

    def _compute_bands(self, samples) -> list:
        '''Split samples into NUM_BANDS sub-chunks and compute normalised RMS.'''
        n = len(samples)
        band_size = max(1, n // self.NUM_BANDS)
        levels = []
        for i in range(self.NUM_BANDS):
            start = i * band_size
            end = min(start + band_size, n)
            chunk = samples[start:end]
            if not chunk:
                levels.append(0.0)
                continue
            rms = math.sqrt(sum(s * s for s in chunk) / len(chunk))
            # Normalise: max s16 = 32767
            level = min(1.0, rms / 12000.0)
            levels.append(level)
        return levels


class EqualizerPopup(QWidget):
    '''Frameless popup drawn above the tray icon with animated equalizer bars
    and a persistent "Playing…" label.'''

    NUM_BARS = 7
    BAR_WIDTH = 6
    BAR_GAP = 3
    EQ_H = 44        # Height for equalizer bars area
    LABEL_H = 18     # Height for text label area
    CORNER_RADIUS = 8

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        total_w = max(
            self.NUM_BARS * self.BAR_WIDTH + (self.NUM_BARS - 1) * self.BAR_GAP + 16,
            100,  # Minimum width for label
        )
        total_h = self.EQ_H + self.LABEL_H
        self.setFixedSize(total_w, total_h)
        self.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._label_text = "Playing…"

        # Current and target bar heights (0.0 → 1.0)
        self._levels = [0.0] * self.NUM_BARS
        self._targets = [0.0] * self.NUM_BARS

        # Smooth animation timer
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate_step)
        self._anim_timer.setInterval(33)  # ~30 fps

    def set_label(self, text: str) -> None:
        '''Update the persistent label text.'''
        self._label_text = text
        self.update()

    def set_levels(self, levels: list) -> None:
        '''Set target levels from audio monitor (0.0-1.0 per band).'''
        for i in range(min(len(levels), self.NUM_BARS)):
            self._targets[i] = levels[i]

    def show_at_tray(self, tray_geometry: QRect) -> None:
        '''Position popup above the tray icon and show it.'''
        if tray_geometry.isValid() and not tray_geometry.isNull() and tray_geometry.width() > 0:
            x = tray_geometry.center().x() - self.width() // 2
            y = tray_geometry.top() - self.height() - 4
            # If tray is at top of screen, show below instead
            if y < 0:
                y = tray_geometry.bottom() + 4
            self.move(x, y)
        else:
            # Fallback: bottom-right corner of primary screen
            screen = QApplication.primaryScreen()
            if screen:
                avail = screen.availableGeometry()
                x = avail.right() - self.width() - 8
                y = avail.bottom() - self.height() - 8
                self.move(x, y)
        self.show()
        self.raise_()
        self._anim_timer.start()

    def hide_popup(self) -> None:
        self._anim_timer.stop()
        self._levels = [0.0] * self.NUM_BARS
        self._targets = [0.0] * self.NUM_BARS
        self.hide()

    def _animate_step(self) -> None:
        '''Smoothly interpolate current levels toward targets.'''
        changed = False
        for i in range(self.NUM_BARS):
            diff = self._targets[i] - self._levels[i]
            if abs(diff) > 0.005:
                # Fast attack, slower decay
                speed = 0.35 if diff > 0 else 0.18
                self._levels[i] += diff * speed
                changed = True
            else:
                if self._levels[i] != self._targets[i]:
                    self._levels[i] = self._targets[i]
                    changed = True
        if changed:
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background with rounded corners
        bg_path = QPainterPath()
        bg_path.addRoundedRect(0.0, 0.0, self.width(), self.height(),
                               self.CORNER_RADIUS, self.CORNER_RADIUS)
        bg_color = QColor(30, 30, 30, 210)
        p.fillPath(bg_path, bg_color)

        # Draw equalizer bars in top area
        margin_x = (self.width() - (self.NUM_BARS * self.BAR_WIDTH + (self.NUM_BARS - 1) * self.BAR_GAP)) // 2
        bar_area_h = self.EQ_H - 16  # vertical padding
        base_y = self.EQ_H - 4

        for i in range(self.NUM_BARS):
            x = margin_x + i * (self.BAR_WIDTH + self.BAR_GAP)
            level = max(0.05, self._levels[i])  # Minimum visible height
            bar_h = int(level * bar_area_h)

            # Gradient: green at bottom → yellow → orange at top
            grad = QLinearGradient(x, base_y, x, base_y - bar_h)
            grad.setColorAt(0.0, QColor(76, 175, 80))    # green
            grad.setColorAt(0.5, QColor(255, 235, 59))   # yellow
            grad.setColorAt(1.0, QColor(255, 87, 34))    # orange-red

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            bar_path = QPainterPath()
            bar_path.addRoundedRect(
                float(x), float(base_y - bar_h),
                float(self.BAR_WIDTH), float(bar_h),
                2.0, 2.0,
            )
            p.drawPath(bar_path)

        # Draw label text below bars
        if self._label_text:
            from PySide6.QtGui import QFont
            font = QFont()
            font.setPointSize(8)
            font.setBold(True)
            p.setFont(font)
            p.setPen(QColor(220, 220, 220))
            label_rect = QRect(4, self.EQ_H, self.width() - 8, self.LABEL_H)
            p.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, self._label_text)

        p.end()


try:
    # argv: icon_name, title, tooltip, icon_dark_path, icon_light_path
    title       = sys.argv[1] if len(sys.argv) > 1 else "App"
    tooltip     = sys.argv[2] if len(sys.argv) > 2 else title
    icon_dark   = sys.argv[3] if len(sys.argv) > 3 else ""   # for dark bg (white icon)
    icon_light  = sys.argv[4] if len(sys.argv) > 4 else ""   # for light bg (dark icon)

    sys.argv[0] = title
    app = QApplication(sys.argv)
    app.setApplicationName(title)
    app.setDesktopFileName("br.com.biglinux.tts")
    app.setQuitOnLastWindowClosed(False)

    def is_dark_theme() -> bool:
        '''Detect if the current system palette is dark.'''
        palette = app.palette()
        bg = palette.window().color()
        return bg.lightness() < 128

    def get_icon_for_theme() -> "QIcon":
        '''Return white icon for dark bg, dark icon for light bg.'''
        if is_dark_theme():
            path = icon_dark
        else:
            path = icon_light
        if path:
            return QIcon(path)
        return QIcon.fromTheme("tts-biglinux-symbolic")

    icon = get_icon_for_theme()
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip(tooltip)

    def update_icon_from_theme():
        '''Reload icon when system palette changes.'''
        new_icon = get_icon_for_theme()
        tray.setIcon(new_icon)

    app.paletteChanged.connect(lambda _: update_icon_from_theme())

    menu = QMenu()
    tray.setContextMenu(menu)

    # ── Equalizer popup & audio monitor ──
    eq_popup = EqualizerPopup()
    audio_signal = AudioLevelSignal()
    audio_monitor = AudioMonitor(audio_signal)

    def _on_levels(levels: list) -> None:
        eq_popup.set_levels(levels)

    audio_signal.levels_ready.connect(_on_levels)

    action_map: dict = {}

    def on_activated(reason) -> None:
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
                audio_monitor.stop()
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
            elif cmd == "set_speaking":
                speaking = msg.get("speaking", False)
                label = msg.get("label", "Playing…")
                if speaking:
                    tray.setToolTip(label)
                    eq_popup.set_label(label)
                    tray_geo = tray.geometry()
                    eq_popup.show_at_tray(tray_geo)
                    audio_monitor.start()
                else:
                    audio_monitor.stop()
                    eq_popup.hide_popup()
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
        audio_monitor.stop()
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

    def set_speaking(self, speaking: bool, label: str = "") -> None:
        """Update tray icon tooltip and equalizer popup while speaking."""
        msg: dict = {"cmd": "set_speaking", "speaking": speaking}
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
                    item_id = msg.get("id")
                    for m in self._menu_items:
                        if m.item_id == item_id and m.callback:
                            m.callback()
                            break
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
