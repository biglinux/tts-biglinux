"""
Global shortcut: one place to parse, show, validate, register and verify it.

The configured shortcut is stored as a GTK accelerator (``<Alt>v``). Every
part of the UI renders it through :func:`keycap_labels`, and registration
reports back what the desktop actually accepted, so the app never claims a
shortcut works before the system confirms it.

KDE Plasma is driven through KGlobalAccel's D-Bus API: ``setForeignShortcut``
assigns the key to the ``biglinux-tts-speak.desktop`` service launcher and
kglobalacceld persists it to ``kglobalshortcutsrc`` itself; ``allShortcutInfos``
reads back the key that is really active and ``getGlobalShortcutsByKey`` finds
other actions that already use it. Other desktops use their own mechanisms in
DesktopIntegrationService.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from utils.i18n import _

logger = logging.getLogger(__name__)

# KGlobalAccel identifiers of the launcher (service .desktop file).
KDE_COMPONENT = "biglinux-tts-speak.desktop"
KDE_COMPONENT_PATH = "/component/biglinux_tts_speak_desktop"
KDE_ACTION = "_launch"
KDE_FRIENDLY = "BigLinux TTS"

# Qt key-code modifier bits (Qt::KeyboardModifier).
_QT_SHIFT = 0x02000000
_QT_CTRL = 0x04000000
_QT_ALT = 0x08000000
_QT_META = 0x10000000

# GDK keyval name → Qt::Key for non-printable keys.
_QT_NAMED_KEYS = {
    "Escape": 0x01000000, "Tab": 0x01000001, "BackSpace": 0x01000003,
    "Return": 0x01000004, "KP_Enter": 0x01000005, "Insert": 0x01000006,
    "Delete": 0x01000007, "Pause": 0x01000008, "Print": 0x01000009,
    "Home": 0x01000010, "End": 0x01000011, "Left": 0x01000012,
    "Up": 0x01000013, "Right": 0x01000014, "Down": 0x01000015,
    "Page_Up": 0x01000016, "Page_Down": 0x01000017, "space": 0x20,
}


@dataclass
class ShortcutStatus:
    """What the desktop reported for the configured shortcut."""

    accel: str = ""
    registered: bool | None = None  # None: not checked yet / unknown desktop
    message: str = ""  # translated explanation when not registered
    conflicts: list[str] = field(default_factory=list)  # other actions on the key


def _parse(accel: str) -> tuple[int, object]:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gtk

    ok = Gtk.accelerator_parse(accel)
    # PyGObject returns (ok, key, mods) on GTK 4.
    if len(ok) == 3:
        valid, key, mods = ok
        if not valid:
            return 0, 0
        return key, mods
    key, mods = ok
    return key, mods


def keycap_labels(accel: str) -> list[str]:
    """``<Control><Alt>v`` → ``["Ctrl", "Alt", "V"]`` for keycap display.

    Modifier and key names come from GTK so they follow the desktop language
    (e.g. "Space", "Espaço"). Returns [] for an empty or invalid accelerator.
    """
    if not accel:
        return []
    from gi.repository import Gdk, Gtk

    key, mods = _parse(accel)
    if not key:
        return []
    parts: list[str] = []
    for mask in (
        Gdk.ModifierType.CONTROL_MASK,
        Gdk.ModifierType.ALT_MASK,
        Gdk.ModifierType.SHIFT_MASK,
        Gdk.ModifierType.SUPER_MASK,
    ):
        if mods & mask:
            parts.append(Gtk.accelerator_get_label(0, mask).rstrip("+") or "?")
    key_label = Gtk.accelerator_get_label(key, 0)
    parts.append(key_label.upper() if len(key_label) == 1 else key_label)
    return parts


def display_text(accel: str) -> str:
    """Single-line form used inside sentences: ``Alt+V``."""
    return "+".join(keycap_labels(accel))


def validate(accel: str) -> str:
    """Why ``accel`` cannot be a global shortcut ("" if it can).

    A global shortcut without Ctrl, Alt or Super would steal a key from every
    application (typing "v" would read text), so one of them is required —
    except for function keys, which apps rarely use for text input.
    """
    from gi.repository import Gdk

    key, mods = _parse(accel)
    if not key:
        return _("This key combination is not valid.")
    name = Gdk.keyval_name(key) or ""
    is_function_key = name.startswith("F") and name[1:].isdigit()
    strong = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK
    if not (mods & strong) and not is_function_key:
        return _("Use Ctrl, Alt or Super together with a key, so normal typing is not affected.")
    return ""


def to_qt_key(accel: str) -> int:
    """GTK accelerator → Qt key code (as KGlobalAccel stores keys); 0 if unknown."""
    from gi.repository import Gdk

    key, mods = _parse(accel)
    if not key:
        return 0
    name = Gdk.keyval_name(key) or ""
    if name in _QT_NAMED_KEYS:
        code = _QT_NAMED_KEYS[name]
    elif name.startswith("F") and name[1:].isdigit():
        code = 0x01000030 + int(name[1:]) - 1
    else:
        char = Gdk.keyval_to_unicode(Gdk.keyval_to_upper(key))
        if not char:
            return 0
        code = char
    if mods & Gdk.ModifierType.SHIFT_MASK:
        code |= _QT_SHIFT
    if mods & Gdk.ModifierType.CONTROL_MASK:
        code |= _QT_CTRL
    if mods & Gdk.ModifierType.ALT_MASK:
        code |= _QT_ALT
    if mods & Gdk.ModifierType.SUPER_MASK:
        code |= _QT_META
    return code


# ── KDE Plasma (KGlobalAccel over D-Bus) ─────────────────────────────


def _kga_call(path: str, iface: str, method: str, params=None, timeout_ms: int = 3000):
    from gi.repository import Gio

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return bus.call_sync(
        "org.kde.kglobalaccel", path, iface, method, params, None,
        Gio.DBusCallFlags.NONE, timeout_ms, None,
    )


def kde_active_keys(raw: bool = False) -> list[int] | None:
    """Keys KGlobalAccel has active for our launcher (None if unavailable).

    ``raw`` keeps empty (0) slots, e.g. left over from an old malformed
    config value, so they can be cleaned up.
    """
    try:
        infos = _kga_call(
            KDE_COMPONENT_PATH, "org.kde.kglobalaccel.Component", "allShortcutInfos",
        ).unpack()[0]
    except Exception as e:
        logger.debug("KGlobalAccel component not readable: %s", e)
        return None
    for info in infos:
        if info[0] == KDE_ACTION:
            return list(info[6]) if raw else [k for k in info[6] if k]
    return []


def kde_conflicts(qt_key: int) -> list[str]:
    """Friendly names of OTHER actions that already use ``qt_key``."""
    from gi.repository import GLib

    if not qt_key:
        return []
    try:
        infos = _kga_call(
            "/kglobalaccel", "org.kde.KGlobalAccel", "getGlobalShortcutsByKey",
            GLib.Variant("(i)", (qt_key,)),
        ).unpack()[0]
    except Exception as e:
        logger.debug("Conflict lookup failed: %s", e)
        return []
    names = []
    for info in infos:
        action, action_name, component, component_name = info[0], info[1], info[2], info[3]
        if component == KDE_COMPONENT:
            continue
        label = f"{component_name} — {action_name}" if action_name and action_name != component_name else (component_name or action)
        names.append(label)
    return names


def register_kde(accel: str) -> ShortcutStatus:
    """Assign ``accel`` to the launcher in KGlobalAccel and verify it.

    Blocking (D-Bus round trips): call from a worker thread.
    """
    from gi.repository import GLib

    from services.desktop_integration_service import DesktopIntegrationService

    status = ShortcutStatus(accel=accel)
    qt_key = to_qt_key(accel)
    if not qt_key:
        status.registered = False
        status.message = _("This key combination is not supported by the desktop.")
        return status

    # The service .desktop file is what KGlobalAccel launches; keep its
    # X-KDE-Shortcuts in sync, and drop old components holding the key.
    kde_key = DesktopIntegrationService.gtk_accel_to_kde(accel)
    DesktopIntegrationService.ensure_desktop_file(kde_key)
    DesktopIntegrationService.purge_stale_kde_components()

    status.conflicts = kde_conflicts(qt_key)
    if status.conflicts:
        status.registered = False
        status.message = _("{shortcut} is already used by: {names}. Choose another shortcut.").format(
            shortcut=display_text(accel), names=", ".join(status.conflicts)
        )
        return status

    if kde_active_keys(raw=True) == [qt_key]:
        status.registered = True  # already exactly right: no churn at every start
        return status

    action_id = GLib.Variant("(as)", ([KDE_COMPONENT, KDE_ACTION, KDE_FRIENDLY, KDE_FRIENDLY],))
    try:
        _kga_call("/kglobalaccel", "org.kde.KGlobalAccel", "doRegister", action_id)
        _kga_call(
            "/kglobalaccel", "org.kde.KGlobalAccel", "setForeignShortcut",
            GLib.Variant("(asai)", ([KDE_COMPONENT, KDE_ACTION, KDE_FRIENDLY, KDE_FRIENDLY], [qt_key])),
        )
    except Exception as e:
        logger.warning("KGlobalAccel registration failed: %s", e)
        status.registered = False
        status.message = _("The desktop did not accept the shortcut. Is the KDE global shortcuts service running?")
        return status

    active = kde_active_keys()
    status.registered = active == [qt_key]
    if not status.registered:
        status.message = _("The desktop did not confirm the new shortcut.")
    logger.info("KDE shortcut %s registered=%s (active=%s)", accel, status.registered, active)
    return status


def register(accel: str) -> ShortcutStatus:
    """Register ``accel`` on the current desktop and report the outcome.

    Blocking: call from a worker thread.
    """
    from services.desktop_integration_service import DesktopIntegrationService

    problem = validate(accel)
    if problem:
        return ShortcutStatus(accel=accel, registered=False, message=problem)

    de = DesktopIntegrationService.detect_desktop_environment()
    if de == "kde":
        status = register_kde(accel)
        if status.registered is False and not status.conflicts and kde_active_keys() is None:
            # KGlobalAccel not reachable over D-Bus: fall back to the config
            # file + reparse path (older Plasma).
            DesktopIntegrationService.update_khotkeys(accel)
            status.message = _("The shortcut was saved, but the desktop could not confirm it yet.")
        return status
    try:
        ok = DesktopIntegrationService.register_shortcut_for_current_de(accel)
    except Exception as e:
        logger.warning("Shortcut registration failed: %s", e)
        ok = False
    if ok:
        return ShortcutStatus(accel=accel, registered=True)
    return ShortcutStatus(
        accel=accel,
        registered=False,
        message=_("Could not register the shortcut on this desktop. Add it in the system keyboard settings with the command biglinux-tts-speak."),
    )


__all__ = [
    "ShortcutStatus",
    "display_text",
    "keycap_labels",
    "kde_active_keys",
    "kde_conflicts",
    "register",
    "to_qt_key",
    "validate",
]
