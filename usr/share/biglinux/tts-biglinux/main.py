"""Entry point for BigLinux TTS application."""

from __future__ import annotations

import argparse
import logging
import sys


def main() -> None:
    """Application entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        prog="biglinux-tts",
        description="BigLinux Text-to-Speech — Read selected text aloud",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0",
    )

    args = parser.parse_args()

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
    sys.exit(app.run(sys.argv[:1]))  # Pass only program name to GTK


if __name__ == "__main__":
    main()
