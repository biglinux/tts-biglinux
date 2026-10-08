"""Tray icon breathing: only opacity changes, never the icon's size.

Regression: on a scaled screen the pulse frames were painted into a canvas
that lost the source devicePixelRatio, so the icon shrank (~57 % at 175 %).
Runs Qt offscreen; the frame module has no GTK dependency.
"""
import importlib.util
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "usr" / "share" / "biglinux" / "tts-biglinux"
_spec = importlib.util.spec_from_file_location("tray_icon_frames", APP / "services" / "tray_icon_frames.py")
tif = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tif)

ICON_SVG = Path(__file__).resolve().parent.parent / "usr" / "share" / "icons" / "hicolor" / "scalable" / "status" / "tts-biglinux-dark.svg"


@pytest.fixture(scope="module")
def icon():
    _app = QGuiApplication.instance() or QGuiApplication([])
    if not ICON_SVG.exists():
        pytest.skip("tray icon SVG not found")
    ic = QIcon(str(ICON_SVG))
    assert not ic.pixmap(QSize(32, 32)).isNull()
    yield ic
    del _app


def _content_box(pm: QPixmap) -> tuple[int, int, int, int]:
    """Bounding box of visible pixels, in physical pixels."""
    img = pm.toImage()
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > 10:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 1.75, 2.0])
@pytest.mark.parametrize("opacity", [tif.MIN_OPACITY, 0.6, 1.0])
def test_frames_keep_the_original_size_and_position(icon, dpr, opacity):
    frame = tif.render_frame(icon, opacity, [dpr])
    for size in (16, 22, 32, 48):
        original = icon.pixmap(QSize(size, size), dpr)
        rendered = frame.pixmap(QSize(size, size), dpr)
        assert rendered.size() == original.size()
        assert rendered.devicePixelRatio() == pytest.approx(original.devicePixelRatio())
        # Same visible area, same place: only alpha may differ. One pixel of
        # tolerance: faint antialiased edges can drop under the alpha cut-off
        # (the regression shrank the drawing by ~40 %).
        for got, want in zip(_content_box(rendered), _content_box(original)):
            assert abs(got - want) <= 1


def test_the_old_way_shrank_the_icon_on_scaled_screens(icon):
    """Documents the root cause the frames avoid (175 % screen)."""
    src = icon.pixmap(QSize(64, 64), 1.75)
    old = QPixmap(src.size())  # devicePixelRatio lost (1.0)
    old.fill(Qt.GlobalColor.transparent)
    p = QPainter(old)
    p.drawPixmap(0, 0, src)
    p.end()
    x0, y0, x1, y1 = _content_box(old)
    ox0, oy0, ox1, oy1 = _content_box(src)
    assert (x1 - x0) < 0.7 * (ox1 - ox0)  # visibly smaller: the regression


def test_only_alpha_changes_between_levels(icon):
    frames = tif.FrameSet(icon, [1.0, 1.75])
    full = frames.full().pixmap(QSize(32, 32), 1.75).toImage()
    faint = frames.frame(0).pixmap(QSize(32, 32), 1.75).toImage()
    assert full.size() == faint.size()
    # The faint frame is never brighter/more opaque than the full one.
    for y in range(0, full.height(), 3):
        for x in range(0, full.width(), 3):
            assert faint.pixelColor(x, y).alpha() <= full.pixelColor(x, y).alpha()


def test_frames_are_cached(icon):
    frames = tif.FrameSet(icon, [1.0])
    assert frames.frame(5) is frames.frame(5)


def test_breathing_is_smooth_and_bounded():
    b = tif.Breather()
    b.start()
    prev = b.opacity
    seen = []
    for _ in range(int(3 * tif.CYCLE_MS / tif.TICK_MS)):
        op, running = b.tick()
        assert running
        assert tif.MIN_OPACITY - 1e-9 <= op <= 1.0 + 1e-9
        assert abs(op - prev) < 0.1  # no abrupt jumps between frames
        prev = op
        seen.append(op)
    assert min(seen) < 0.4 and max(seen) > 0.97  # it really breathes


def test_stop_fades_back_and_ends_the_timer():
    b = tif.Breather()
    b.start()
    for _ in range(9):  # somewhere in the faint part
        b.tick()
    assert b.opacity < 0.8
    b.stop()
    ticks, running, prev = 0, True, b.opacity
    while running:
        op, running = b.tick()
        assert op >= prev  # monotonic return, no flash
        prev = op
        ticks += 1
        assert ticks <= tif.FADE_MS / tif.TICK_MS + 2
    assert b.opacity == 1.0 and not b.running
    assert tif.level_for(b.opacity) == tif.LEVELS - 1  # idle = the 100 % frame


def test_pause_settles_dim_and_resume_continues_without_a_jump():
    b = tif.Breather()
    b.start()
    b.tick()
    b.pause()
    while b.running:
        b.tick()
    assert b.opacity == pytest.approx(tif.PAUSED_OPACITY)
    b.start()
    op, _ = b.tick()
    assert abs(op - tif.PAUSED_OPACITY) < 0.1


def test_idle_stop_does_not_start_a_timer():
    b = tif.Breather()
    b.stop()
    assert not b.running


def test_reduced_motion_holds_a_steady_level():
    b = tif.Breather()
    b.hold(tif.STEADY_OPACITY)
    opacity, running = b.tick()
    assert opacity == tif.STEADY_OPACITY and not running
