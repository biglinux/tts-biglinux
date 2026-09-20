"""Entry point for BigLinux TTS application."""

from __future__ import annotations

import argparse
import logging
import sys

from config import APP_VERSION
from utils.i18n import _


def main() -> None:
    """Application entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        prog="biglinux-tts",
        description=_("BigLinux Text-to-Speech — Read selected text aloud"),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=_("Enable debug logging"),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {APP_VERSION}",
    )

    args, unknown = parser.parse_known_args()

    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    logger = logging.getLogger(__name__)
    logger.debug("Starting BigLinux TTS")

    # Import GTK after logging is configured
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")

    from application import TTSApplication

    app = TTSApplication()
    
    # Pass the program name and any unparsed arguments to GTK GApplication
    gtk_args = [sys.argv[0]] + unknown
    sys.exit(app.run(gtk_args))


if __name__ == "__main__":
    main()
