"""
Inline audio player widget — GStreamer-based mini player.

Provides play/pause, stop, seek bar, and time display.
"""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst, Gtk

from utils.i18n import _

logger = logging.getLogger(__name__)

Gst.init(None)


def _format_time(ns: int) -> str:
    """Format nanoseconds as MM:SS."""
    seconds = max(0, ns // Gst.SECOND)
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


class AudioPlayerWidget(Gtk.Box):
    """Compact audio player with play/pause, stop, seek, and time display."""

    _active_player: AudioPlayerWidget | None = None
    _playback_mode: str = "interrupt"  # interrupt | queue | simultaneous
    _queue: list[AudioPlayerWidget] = []

    @classmethod
    def set_playback_mode(cls, mode: str) -> None:
        cls._playback_mode = mode

    def __init__(self, audio_path: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("player-controls")

        self._audio_path = audio_path
        self._duration_ns: int = 0
        self._poll_id: int = 0
        self._seeking = False
        self._playing = False

        self._pipeline = Gst.ElementFactory.make("playbin", None)
        if self._pipeline:
            self._pipeline.set_property("uri", Path(audio_path).as_uri())
            bus = self._pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_eos)
            bus.connect("message::error", self._on_error)
            bus.connect("message::state-changed", self._on_state_changed)

        self._build_ui()

    def _build_ui(self) -> None:
        """Build player controls."""
        # Seek bar row
        seek_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        seek_box.set_hexpand(True)

        # Play/Pause toggle
        self._play_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self._play_btn.add_css_class("flat")
        self._play_btn.add_css_class("circular")
        self._play_btn.set_tooltip_text(_("Play"))
        self._play_btn.connect("clicked", self._on_play_pause)
        seek_box.append(self._play_btn)

        # Stop button
        self._stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic")
        self._stop_btn.add_css_class("flat")
        self._stop_btn.add_css_class("circular")
        self._stop_btn.set_tooltip_text(_("Stop"))
        self._stop_btn.set_sensitive(False)
        self._stop_btn.connect("clicked", self._on_stop)
        seek_box.append(self._stop_btn)

        # Elapsed time
        self._elapsed_label = Gtk.Label(label="00:00")
        self._elapsed_label.add_css_class("player-time")
        self._elapsed_label.add_css_class("caption")
        seek_box.append(self._elapsed_label)

        # Seek scale
        self._seek_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 1, 0.01
        )
        self._seek_scale.set_draw_value(False)
        self._seek_scale.set_hexpand(True)
        self._seek_scale.add_css_class("player-seek")
        self._seek_scale.connect(
            "change-value", self._on_seek_change
        )
        seek_box.append(self._seek_scale)

        # Total time
        self._total_label = Gtk.Label(label="00:00")
        self._total_label.add_css_class("player-time")
        self._total_label.add_css_class("caption")
        seek_box.append(self._total_label)

        self.append(seek_box)

    # ── Playback Controls ────────────────────────────────────────

    def _on_play_pause(self, _btn: Gtk.Button) -> None:
        if not self._pipeline:
            return

        if self._playing:
            # Pause
            self._pipeline.set_state(Gst.State.PAUSED)
            self._playing = False
            self._play_btn.set_icon_name("media-playback-start-symbolic")
            self._play_btn.set_tooltip_text(_("Play"))
            return

        mode = AudioPlayerWidget._playback_mode
        prev = AudioPlayerWidget._active_player

        if mode == "interrupt":
            # Stop any other active player
            if prev is not None and prev is not self:
                prev._force_stop()
            AudioPlayerWidget._active_player = self
        elif mode == "queue":
            # If another player is active, enqueue self and return
            if prev is not None and prev is not self and prev._playing:
                if self not in AudioPlayerWidget._queue:
                    AudioPlayerWidget._queue.append(self)
                return
            AudioPlayerWidget._active_player = self
        # simultaneous: just play, no stop

        self._pipeline.set_state(Gst.State.PLAYING)
        self._playing = True
        self._play_btn.set_icon_name("media-playback-pause-symbolic")
        self._play_btn.set_tooltip_text(_("Pause"))
        self._stop_btn.set_sensitive(True)
        self._start_poll()

    def _on_stop(self, _btn: Gtk.Button | None = None) -> None:
        self._force_stop()

    def _force_stop(self) -> None:
        if not self._pipeline:
            return
        self._stop_poll()
        self._pipeline.set_state(Gst.State.NULL)
        self._playing = False
        self._play_btn.set_icon_name("media-playback-start-symbolic")
        self._play_btn.set_tooltip_text(_("Play"))
        self._stop_btn.set_sensitive(False)
        self._seek_scale.set_value(0)
        self._elapsed_label.set_label("00:00")
        if AudioPlayerWidget._active_player is self:
            AudioPlayerWidget._active_player = None

    def _on_seek_change(
        self,
        _scale: Gtk.Scale,
        _scroll: Gtk.ScrollType,
        value: float,
    ) -> bool:
        if not self._pipeline or self._duration_ns <= 0:
            return False
        seek_ns = int(value * self._duration_ns)
        self._pipeline.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            seek_ns,
        )
        self._elapsed_label.set_label(_format_time(seek_ns))
        return False

    # ── GStreamer Signals ────────────────────────────────────────

    def _on_eos(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        GLib.idle_add(self._on_eos_idle)

    def _on_eos_idle(self) -> None:
        self._force_stop()
        # Queue mode: play next queued item
        if AudioPlayerWidget._queue:
            nxt = AudioPlayerWidget._queue.pop(0)
            if nxt._pipeline:
                nxt._on_play_pause(None)

    def _on_error(self, _bus: Gst.Bus, msg: Gst.Message) -> None:
        err, debug = msg.parse_error()
        logger.error("GStreamer error: %s (%s)", err.message, debug)
        GLib.idle_add(self._force_stop)

    def _on_state_changed(
        self, _bus: Gst.Bus, msg: Gst.Message
    ) -> None:
        if msg.src != self._pipeline:
            return
        _, new, _ = msg.parse_state_changed()
        if new == Gst.State.PLAYING and self._duration_ns <= 0:
            ok, dur = self._pipeline.query_duration(Gst.Format.TIME)
            if ok and dur > 0:
                self._duration_ns = dur
                GLib.idle_add(
                    self._total_label.set_label,
                    _format_time(dur),
                )

    # ── Position Polling ─────────────────────────────────────────

    def _start_poll(self) -> None:
        if self._poll_id == 0:
            self._poll_id = GLib.timeout_add(250, self._poll_position)

    def _stop_poll(self) -> None:
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0

    def _poll_position(self) -> bool:
        if not self._pipeline or not self._playing:
            self._poll_id = 0
            return False

        ok, pos = self._pipeline.query_position(Gst.Format.TIME)
        if ok and self._duration_ns > 0:
            fraction = pos / self._duration_ns
            self._seek_scale.set_value(min(fraction, 1.0))
            self._elapsed_label.set_label(_format_time(pos))

        # Re-query duration if still unknown
        if self._duration_ns <= 0:
            ok_d, dur = self._pipeline.query_duration(Gst.Format.TIME)
            if ok_d and dur > 0:
                self._duration_ns = dur
                self._total_label.set_label(_format_time(dur))

        return True  # keep polling

    # ── Cleanup ──────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Release GStreamer resources."""
        self._stop_poll()
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        self._playing = False
        if AudioPlayerWidget._active_player is self:
            AudioPlayerWidget._active_player = None
        if self in AudioPlayerWidget._queue:
            AudioPlayerWidget._queue.remove(self)
