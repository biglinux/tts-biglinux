"""
Tray icon "breathing" while reading: opacity changes only, never size.

Loaded by the PySide6 tray helper (services/tray_service.py) by file path, so
it imports nothing from GTK or from the rest of the app.

Why frames are built this way
-----------------------------
On a scaled screen (e.g. 175 %) ``QIcon.pixmap(QSize(64, 64))`` returns a
112×112 pixmap whose devicePixelRatio is 1.75. Painting it into a plain
``QPixmap(112, 112)`` (devicePixelRatio 1) draws it at its *logical* 64×64
size in the corner of a 112×112 canvas: the icon shrank to ~57 % on every
pulse frame. Each frame here is rendered from the original icon at every
size and every screen scale, into a canvas with the same pixel size AND the
same devicePixelRatio, so the drawing covers exactly the same area as the
original. The idle icon is the 100 % frame, so idle and animated frames
share the same framing pixel for pixel.

Frames are rendered once per opacity level and cached; the timer only swaps
ready icons, and an unchanged level is not sent to the panel again.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

# Logical sizes a tray may ask for (Plasma uses 16–48; each is also rendered
# at every screen scale). Each frame change sends ALL of them to the panel
# over D-Bus, so the list stays short: 16–128 at two scales cost plasmashell
# ~5 % CPU while breathing.
SIZES = (16, 22, 24, 32, 48)

MIN_OPACITY = 0.35  # faintest point of the breath (never invisible)
PAUSED_OPACITY = 0.45  # steady dim while paused
STEADY_OPACITY = 0.65  # reading, when the desktop asks for reduced motion
LEVELS = 24  # distinct opacity steps between MIN_OPACITY and 1.0
CYCLE_MS = 2400  # one full breath: bright → faint → bright
TICK_MS = 100  # 10 updates/s: smooth enough, light on D-Bus and CPU
FADE_MS = 450  # back to full opacity (or to paused) after the reading


def breath_opacity(t_ms: float) -> float:
    """Opacity at ``t_ms`` into the breath: 1.0 at t=0, MIN at half cycle."""
    phase = 2.0 * math.pi * ((t_ms % CYCLE_MS) / CYCLE_MS)
    return MIN_OPACITY + (1.0 - MIN_OPACITY) * (0.5 + 0.5 * math.cos(phase))


def time_for_opacity(opacity: float) -> float:
    """A point of the breath with this opacity (to resume without a jump)."""
    x = (min(1.0, max(MIN_OPACITY, opacity)) - MIN_OPACITY) / (1.0 - MIN_OPACITY)
    return CYCLE_MS * math.acos(2.0 * x - 1.0) / (2.0 * math.pi)


def level_for(opacity: float) -> int:
    """Quantize an opacity to one of LEVELS cached frames (LEVELS-1 = 100 %)."""
    x = (min(1.0, max(MIN_OPACITY, opacity)) - MIN_OPACITY) / (1.0 - MIN_OPACITY)
    return int(round(x * (LEVELS - 1)))


def opacity_for_level(level: int) -> float:
    return MIN_OPACITY + (1.0 - MIN_OPACITY) * level / (LEVELS - 1)


def render_frame(icon: QIcon, opacity: float, dprs) -> QIcon:
    """``icon`` at ``opacity``, same pixel size and scale as the original."""
    frame = QIcon()
    for dpr in dprs:
        for size in SIZES:
            src = icon.pixmap(QSize(size, size), dpr)
            if src.isNull():
                continue
            out = QPixmap(src.size())
            out.setDevicePixelRatio(src.devicePixelRatio())
            out.fill(Qt.GlobalColor.transparent)
            painter = QPainter(out)
            painter.setOpacity(max(0.0, min(1.0, opacity)))
            painter.drawPixmap(0, 0, src)
            painter.end()
            frame.addPixmap(out)
    return frame


class FrameSet:
    """Cached breathing frames for one icon (rebuild on theme/scale change)."""

    def __init__(self, icon: QIcon, dprs) -> None:
        self.icon = icon
        self.dprs = tuple(sorted({float(d) for d in dprs if d and d > 0} or {1.0}))
        self._frames: dict[int, QIcon] = {}

    def frame(self, level: int) -> QIcon:
        level = max(0, min(LEVELS - 1, level))
        cached = self._frames.get(level)
        if cached is None:
            cached = render_frame(self.icon, opacity_for_level(level), self.dprs)
            self._frames[level] = cached
        return cached

    def full(self) -> QIcon:
        return self.frame(LEVELS - 1)


class Breather:
    """Opacity over time for the reading states (no Qt timer inside).

    ``tick(dt_ms)`` returns the opacity to show and whether the timer must
    keep running; it returns ``running=False`` as soon as nothing moves, so
    no timer is left running when the reading has ended or is paused.
    """

    def __init__(self) -> None:
        self.opacity = 1.0
        self.mode = "idle"  # idle | breathing | fading
        self._t = 0.0
        self._target = 1.0

    def start(self) -> None:
        """Reading started (or resumed): breathe, starting from the current opacity."""
        self._t = time_for_opacity(self.opacity)
        self.mode = "breathing"

    def stop(self) -> None:
        """Reading ended: return smoothly to full opacity."""
        self._fade_to(1.0)

    def pause(self) -> None:
        """Paused: settle on a steady dim icon."""
        self._fade_to(PAUSED_OPACITY)

    def hold(self, opacity: float) -> None:
        """Show ``opacity`` at once, without any animation (reduced motion)."""
        self.opacity = opacity
        self.mode = "idle"

    def _fade_to(self, target: float) -> None:
        self._target = target
        self.mode = "idle" if abs(self.opacity - target) < 1e-3 else "fading"

    @property
    def running(self) -> bool:
        return self.mode != "idle"

    def tick(self, dt_ms: float = TICK_MS) -> tuple[float, bool]:
        if self.mode == "breathing":
            self._t += dt_ms
            self.opacity = breath_opacity(self._t)
        elif self.mode == "fading":
            step = (1.0 - MIN_OPACITY) * dt_ms / FADE_MS
            delta = self._target - self.opacity
            if abs(delta) <= step:
                self.opacity = self._target
                self.mode = "idle"
            else:
                self.opacity += step if delta > 0 else -step
        return self.opacity, self.running
