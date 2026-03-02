"""
Clipboard service — capture selected text from Wayland or X11.

Detects the display server and uses the appropriate clipboard tool.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import NamedTuple

logger = logging.getLogger(__name__)


class ClipboardResult(NamedTuple):
    """Result of a clipboard capture attempt."""

    text: str
    success: bool
    error: str


def is_wayland() -> bool:
    """Detect if running on Wayland."""
    return (
        os.environ.get("XDG_SESSION_TYPE") == "wayland"
        or os.environ.get("WAYLAND_DISPLAY") is not None
    )


def get_selected_text(max_chars: int = 10000) -> ClipboardResult:
    """
    Capture the currently selected (primary selection) text.

    Tries primary selection first, falls back to clipboard.
    Supports both Wayland and X11.

    Args:
        max_chars: Maximum characters to return (safety limit).

    Returns:
        ClipboardResult with text, success flag, and error message.
    """
    if is_wayland():
        return _get_text_wayland(max_chars)
    return _get_text_x11(max_chars)


def _get_text_wayland(max_chars: int) -> ClipboardResult:
    """Capture text on Wayland via wl-paste."""
    if not shutil.which("wl-paste"):
        return ClipboardResult("", False, "wl-clipboard not installed")

    # Try primary selection first, then regular clipboard
    for args in [["wl-paste", "--primary", "--no-newline"], ["wl-paste", "--no-newline"]]:
        result = _run_capture(args, max_chars)
        if result.success and result.text:
            return result

    return ClipboardResult("", False, "no-text-selected")


def _get_text_x11(max_chars: int) -> ClipboardResult:
    """Capture text on X11 via xsel or xclip."""
    # Try xsel first
    if shutil.which("xsel"):
        for args in [["xsel", "--primary", "-o"], ["xsel", "-o"]]:
            result = _run_capture(args, max_chars)
            if result.success and result.text:
                return result

    # Fallback to xclip
    if shutil.which("xclip"):
        for args in [
            ["xclip", "-selection", "primary", "-o"],
            ["xclip", "-selection", "clipboard", "-o"],
        ]:
            result = _run_capture(args, max_chars)
            if result.success and result.text:
                return result

    if not shutil.which("xsel") and not shutil.which("xclip"):
        return ClipboardResult("", False, "xsel or xclip not installed")

    return ClipboardResult("", False, "no-text-selected")


def _run_capture(args: list[str], max_chars: int) -> ClipboardResult:
    """Run a clipboard capture command safely."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            text = proc.stdout.strip()
            if text:
                if max_chars > 0 and len(text) > max_chars:
                    text = text[:max_chars]
                    logger.info("Text truncated to %d chars", max_chars)
                return ClipboardResult(text, True, "")
        return ClipboardResult("", False, "empty")
    except subprocess.TimeoutExpired:
        logger.warning("Clipboard command timed out: %s", args[0])
        return ClipboardResult("", False, "timeout")
    except OSError as e:
        logger.warning("Clipboard command failed: %s", e)
        return ClipboardResult("", False, str(e))
