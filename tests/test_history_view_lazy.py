"""Regression: AudioPlayerWidget must NOT build a GStreamer pipeline eagerly.

Building ~2000 playbin pipelines up front was the dominant History freeze cause.
"""
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "usr" / "share" / "biglinux" / "tts-biglinux"
sys.path.insert(0, str(APP))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
try:
    from gi.repository import Gdk, Gtk
    from ui.audio_player import AudioPlayerWidget
except Exception as e:  # pragma: no cover - headless GTK may be unavailable
    pytest.skip(f"GTK/Gst unavailable headless: {e}", allow_module_level=True)

# Without a display, creating any GTK widget aborts the whole process
# (segfault — not an exception), e.g. in the package builder's check().
# Gtk.init_check() alone is not enough: GTK 4.22 returns True with no display.
if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display for GTK", allow_module_level=True)


def test_pipeline_is_lazy():
    try:
        w = AudioPlayerWidget("/tmp/nonexistent.wav")
    except Exception as e:  # widget construction may need a display
        pytest.skip(f"widget construction needs display: {e}")
    assert w._pipeline is None, "pipeline must not be created in __init__ (lazy on play)"
