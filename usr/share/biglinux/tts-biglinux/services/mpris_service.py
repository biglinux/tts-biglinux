"""MPRIS media player integration.

Exposes an ``org.mpris.MediaPlayer2`` D-Bus player so the desktop (KDE media
controls, taskbar previews, GNOME media widget) shows a mini-player with
Play/Stop and a progress bar while text is being read. The player is only
"present" (PlaybackStatus=Playing) while speaking; it goes Stopped when idle so
it disappears from the desktop widgets.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from gi.repository import Gio, GLib

if TYPE_CHECKING:
    from application import TTSApplication

logger = logging.getLogger(__name__)

BUS_NAME = "org.mpris.MediaPlayer2.biglinux-tts"
OBJECT_PATH = "/org/mpris/MediaPlayer2"

_INTROSPECTION = """
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Play"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition">
      <arg direction="in" name="TrackId" type="o"/>
      <arg direction="in" name="Position" type="x"/>
    </method>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Position" type="x" access="read"/>
    <property name="Rate" type="d" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
  </interface>
</node>
"""

_TRACK_ID = "/com/biglinux/tts/track/0"


class MprisService:
    """Registers an MPRIS player and reflects the TTS state onto it."""

    def __init__(self, app: TTSApplication) -> None:
        self._app = app
        self._conn: Gio.DBusConnection | None = None
        self._name_id = 0
        self._reg_ids: list[int] = []
        self._node = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION)

        self._status = "Stopped"
        self._title = ""
        self._length_us = 0
        self._start = 0.0  # monotonic time playback started

    # ── Lifecycle ────────────────────────────────────────────────────

    def enable(self) -> None:
        """Own the MPRIS bus name (idempotent)."""
        if self._name_id:
            return
        self._name_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            None,
            self._on_name_lost,
        )
        logger.debug("MPRIS: owning %s", BUS_NAME)

    def disable(self) -> None:
        """Release the bus name and unregister objects."""
        self.set_stopped()
        for rid in self._reg_ids:
            if self._conn is not None:
                self._conn.unregister_object(rid)
        self._reg_ids.clear()
        if self._name_id:
            Gio.bus_unown_name(self._name_id)
            self._name_id = 0
        self._conn = None
        logger.debug("MPRIS: disabled")

    def _on_bus_acquired(self, conn: Gio.DBusConnection, _name: str) -> None:
        self._conn = conn
        try:
            for iface in self._node.interfaces:
                rid = conn.register_object(
                    OBJECT_PATH,
                    iface,
                    self._on_method_call,
                    self._on_get_property,
                    self._on_set_property,
                )
                self._reg_ids.append(rid)
        except Exception as e:
            logger.warning("MPRIS: registration failed: %s", e)

    def _on_name_lost(self, _conn: Gio.DBusConnection | None, _name: str) -> None:
        logger.debug("MPRIS: name lost")

    # ── State updates (called from the app on TTS state change) ──────

    def set_playing(self, title: str, length_us: int = 0) -> None:
        self._title = title or ""
        self._length_us = max(0, int(length_us))
        self._start = time.monotonic()
        self._status = "Playing"
        self._emit_properties({
            "PlaybackStatus": GLib.Variant("s", "Playing"),
            "Metadata": self._metadata_variant(),
            "CanPause": GLib.Variant("b", True),
            "CanPlay": GLib.Variant("b", True),
        })

    def set_stopped(self) -> None:
        if self._status == "Stopped":
            return
        self._status = "Stopped"
        self._emit_properties({
            "PlaybackStatus": GLib.Variant("s", "Stopped"),
            "Metadata": self._metadata_variant(),
        })

    def _position_us(self) -> int:
        if self._status != "Playing":
            return 0
        pos = int((time.monotonic() - self._start) * 1_000_000)
        if self._length_us:
            pos = min(pos, self._length_us)
        return max(0, pos)

    def _metadata_variant(self) -> GLib.Variant:
        meta = {
            "mpris:trackid": GLib.Variant("o", _TRACK_ID),
            "xesam:title": GLib.Variant("s", self._title or "BigLinux TTS"),
            "xesam:artist": GLib.Variant("as", ["BigLinux TTS"]),
        }
        if self._length_us:
            meta["mpris:length"] = GLib.Variant("x", self._length_us)
        return GLib.Variant("a{sv}", meta)

    def _emit_properties(self, changed: dict) -> None:
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(
                None,
                OBJECT_PATH,
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                GLib.Variant(
                    "(sa{sv}as)",
                    ("org.mpris.MediaPlayer2.Player", changed, []),
                ),
            )
        except Exception as e:
            logger.debug("MPRIS: emit failed: %s", e)

    # ── D-Bus handlers ───────────────────────────────────────────────

    def _on_method_call(
        self, _conn, _sender, _path, iface, method, _params, invocation
    ) -> None:
        tts = self._app.tts_service
        if iface == "org.mpris.MediaPlayer2":
            if method == "Quit":
                self._app.quit()
            elif method == "Raise":
                self._app.activate()
            invocation.return_value(None)
            return

        # Player interface
        if method == "Stop":
            GLib.idle_add(tts.stop)
        elif method in ("Pause", "PlayPause") and self._status == "Playing":
            GLib.idle_add(tts.stop)
        elif method in ("Play", "PlayPause") and self._status != "Playing":
            GLib.idle_add(self._replay)
        # Next/Previous/Seek/SetPosition: no-ops for TTS.
        invocation.return_value(None)

    def _replay(self) -> bool:
        """Play/PlayPause when idle → re-read the last spoken text."""
        tts = self._app.tts_service
        text = getattr(tts, "_last_spoken_text", "")
        if text:
            speech = self._app.settings.speech
            tts.speak(
                text,
                rate=speech.rate,
                pitch=speech.pitch,
                volume=speech.volume,
                backend=speech.backend,
                output_module=speech.output_module,
                voice_id=speech.voice_id,
            )
        return False

    def _on_get_property(self, _conn, _sender, _path, iface, prop) -> GLib.Variant:
        if iface == "org.mpris.MediaPlayer2":
            return {
                "CanQuit": GLib.Variant("b", True),
                "CanRaise": GLib.Variant("b", True),
                "HasTrackList": GLib.Variant("b", False),
                "Identity": GLib.Variant("s", "BigLinux TTS"),
                "DesktopEntry": GLib.Variant("s", "br.com.biglinux.tts"),
                "SupportedUriSchemes": GLib.Variant("as", []),
                "SupportedMimeTypes": GLib.Variant("as", []),
            }.get(prop, GLib.Variant("b", False))

        return {
            "PlaybackStatus": GLib.Variant("s", self._status),
            "Metadata": self._metadata_variant(),
            "Position": GLib.Variant("x", self._position_us()),
            "Rate": GLib.Variant("d", 1.0),
            "MinimumRate": GLib.Variant("d", 1.0),
            "MaximumRate": GLib.Variant("d", 1.0),
            "Volume": GLib.Variant("d", 1.0),
            "CanGoNext": GLib.Variant("b", False),
            "CanGoPrevious": GLib.Variant("b", False),
            "CanPlay": GLib.Variant("b", True),
            "CanPause": GLib.Variant("b", True),
            "CanSeek": GLib.Variant("b", False),
            "CanControl": GLib.Variant("b", True),
        }.get(prop, GLib.Variant("b", False))

    def _on_set_property(self, _conn, _sender, _path, _iface, _prop, _value) -> bool:
        return True
