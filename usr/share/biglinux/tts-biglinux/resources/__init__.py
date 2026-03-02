"""Application resources: CSS and icons."""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk

logger = logging.getLogger(__name__)

_RESOURCES_DIR = Path(__file__).parent


def load_css() -> None:
    """Load custom CSS stylesheet into GTK display."""
    css_path = _RESOURCES_DIR / "style.css"
    if not css_path.exists():
        logger.warning("CSS file not found: %s", css_path)
        return

    provider = Gtk.CssProvider()
    provider.load_from_path(str(css_path))

    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        logger.debug("CSS loaded from %s", css_path)
