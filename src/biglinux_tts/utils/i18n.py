"""Internationalization support using gettext."""

from __future__ import annotations

import gettext
import locale
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DOMAIN = "biglinux-tts"

# Try system locale directory first, then development fallback
_locale_dir = Path("/usr/share/locale")
_dev_locale_dir = Path(__file__).parent.parent.parent.parent / "usr" / "share" / "locale"

if not _locale_dir.exists() or not any(_locale_dir.iterdir()):
    _locale_dir = _dev_locale_dir

try:
    locale.setlocale(locale.LC_ALL, "")
except locale.Error:
    logger.debug("Could not set locale, using default")

try:
    _translation = gettext.translation(
        DOMAIN,
        localedir=str(_locale_dir),
        fallback=True,
    )
    _ = _translation.gettext
except Exception:
    logger.debug("Translation not found, using passthrough")

    def _(message: str) -> str:
        return message
