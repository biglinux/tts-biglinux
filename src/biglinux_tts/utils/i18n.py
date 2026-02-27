"""Internationalization support — loads translations from .po files."""

from __future__ import annotations

import locale
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_translations: dict[str, str] = {}


def _parse_po(path: Path) -> dict[str, str]:
    """Parse a .po file and return a msgid→msgstr dict."""
    result: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("msgid "):
            msgid = _extract_string(line[6:])
            i += 1
            while i < len(lines) and lines[i].startswith('"'):
                msgid += _extract_string(lines[i])
                i += 1
            if i < len(lines) and lines[i].startswith("msgstr "):
                msgstr = _extract_string(lines[i][7:])
                i += 1
                while i < len(lines) and lines[i].startswith('"'):
                    msgstr += _extract_string(lines[i])
                    i += 1
                if msgid and msgstr:
                    result[msgid] = msgstr
            continue
        i += 1
    return result


def _extract_string(s: str) -> str:
    """Extract content from a quoted .po string."""
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def _get_locale_candidates() -> list[str]:
    """Return locale codes to try, in priority order."""
    candidates: list[str] = []
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var, "")
        if not val or val == "C" or val == "POSIX":
            continue
        for part in val.split(":"):
            code = part.split(".")[0].split("@")[0]
            if code and code not in candidates:
                candidates.append(code)
            short = code.split("_")[0]
            if short and short not in candidates:
                candidates.append(short)
    return candidates


def _find_po(locale_dir: Path, candidates: list[str]) -> Path | None:
    """Find the best matching .po file."""
    for code in candidates:
        # Try hyphenated form (pt-BR.po) first, then underscore (pt_BR.po)
        for variant in (code.replace("_", "-"), code):
            po = locale_dir / f"{variant}.po"
            if po.is_file():
                return po
    return None


# Locate .po files — try development dir first, then system install
_project_root = Path(__file__).parent.parent.parent.parent
_po_dirs = [
    _project_root / "locale",
    Path("/usr/share/tts-biglinux/locale"),
]

try:
    locale.setlocale(locale.LC_ALL, "")
except locale.Error:
    logger.debug("Could not set locale, using default")

_candidates = _get_locale_candidates()
_po_file = None
for _dir in _po_dirs:
    _po_file = _find_po(_dir, _candidates)
    if _po_file:
        break
if _po_file:
    _translations = _parse_po(_po_file)
    logger.debug("Loaded %d translations from %s", len(_translations), _po_file)
else:
    logger.debug("No .po file found for locales %s in %s", _candidates, _po_dirs)


def _(message: str) -> str:
    return _translations.get(message, message)
