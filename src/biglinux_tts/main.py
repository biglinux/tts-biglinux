"""Entry point for BigLinux TTS application."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _ensure_python_path() -> None:
    """Add src/ to sys.path if running directly."""
    this_file = Path(__file__).resolve()
    # src/biglinux_tts/main.py → src/
    src_dir = str(this_file.parent.parent)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


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

    from biglinux_tts.application import TTSApplication

    app = TTSApplication()
    sys.exit(app.run(sys.argv[:1]))  # Pass only program name to GTK


if __name__ == "__main__":
    _ensure_python_path()
    main()
