"""Internationalization: translations read directly from the .po catalogs.

The catalogs are produced by BigLinux's translation pipeline (plain
``xgettext`` with the default Python keywords, then ``attranslate``), and the
package installs the .po files, not compiled .mo files. Two consequences:

- Every user-visible string must be a literal inside ``_()`` so xgettext can
  extract it; a string the pipeline cannot see is dropped from every catalog
  on the next translation run.
- Language selection follows GNU gettext: ``LC_ALL`` > ``LC_MESSAGES`` >
  ``LANG`` decide the locale; a C/POSIX locale means English and ``LANGUAGE``
  is ignored; otherwise ``LANGUAGE`` (a colon list) wins. Each message falls
  back through the list (and from ``pt_BR`` to ``pt``) before English.
"""

from __future__ import annotations

import functools
import gettext
import json
import locale
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
_RE_ESCAPE = re.compile(r"\\(.)")

# Catalog names that differ from the locale's language code.
_ALIASES = {"nb": "no", "nn": "no"}


def _extract_string(s: str) -> str:
    """Content of one quoted .po string, unescaped in a single pass."""
    s = s.strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return _RE_ESCAPE.sub(lambda m: _ESCAPES.get(m.group(1), m.group(1)), s)


def _parse_po(path: Path) -> dict[str, str]:
    """msgid → msgstr for the usable entries of a .po file.

    Skipped: the header, empty translations, ``#, fuzzy`` entries, entries
    with a ``msgctxt`` (none are used; they must not shadow plain ones) and
    plural entries (``msgstr[n]``; the application does not use plurals).
    """
    result: dict[str, str] = {}
    entry: dict[str, str] = {}
    fuzzy = False
    key = ""

    def complete() -> bool:
        return any(k.startswith("msgstr") for k in entry)

    def flush() -> None:
        msgid, msgstr = entry.get("msgid", ""), entry.get("msgstr", "")
        if msgid and msgstr and not fuzzy and not ({"msgctxt", "msgid_plural"} & entry.keys()):
            result[msgid] = msgstr

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        # A comment or a new msgctxt/msgid after a msgstr starts the next entry
        # (catalogs from the pipeline do not always separate entries by a blank line).
        if complete() and (line.startswith("#") or line.startswith(("msgctxt ", "msgid "))):
            flush()
            entry, fuzzy, key = {}, False, ""
        if line.startswith("#"):  # also skips obsolete "#~" entries
            if line.startswith("#,") and "fuzzy" in line:
                fuzzy = True
            continue
        if line.startswith('"'):
            if key:
                entry[key] += _extract_string(line)
            continue
        key, _sep, rest = line.partition(" ")
        entry[key] = _extract_string(rest)
    if complete():
        flush()
    return result


def _locale_candidates(environ: dict[str, str] | os._Environ[str] = os.environ) -> list[str]:
    """Language codes to look up, best first (GNU gettext precedence)."""
    current = ""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if environ.get(var):
            current = environ[var]
            break
    base = current.split(".")[0].split("@")[0]
    if not base or base in ("C", "POSIX"):
        return []
    codes = [c for c in environ.get("LANGUAGE", "").split(":") if c] or [current]
    candidates: list[str] = []
    for code in codes:
        code = code.split(".")[0].split("@")[0].replace("-", "_")
        if code in ("C", "POSIX"):
            continue
        lang = code.split("_")[0].lower()
        for c in (code, lang, _ALIASES.get(lang, "")):
            if c and c.lower() not in (x.lower() for x in candidates):
                candidates.append(c)
    return candidates


def _find_catalogs(locale_dir: Path, candidates: list[str]) -> list[Path]:
    """The .po files for ``candidates`` (pt_BR, pt-BR, pt_br … all match)."""
    if not candidates or not locale_dir.is_dir():
        return []
    available = {p.stem.lower().replace("-", "_"): p for p in locale_dir.glob("*.po")}
    found: list[Path] = []
    for code in candidates:
        path = available.get(code.lower())
        if path and path not in found:
            found.append(path)
    return found


def _load(po_dirs: list[Path], candidates: list[str]) -> dict[str, str]:
    for po_dir in po_dirs:
        catalogs = _find_catalogs(po_dir, candidates)
        if catalogs:
            merged: dict[str, str] = {}
            for path in reversed(catalogs):  # the best catalog wins
                merged.update(_parse_po(path))
            logger.debug("Loaded %d translations from %s", len(merged), catalogs)
            return merged
    logger.debug("No catalog for %s in %s", candidates, po_dirs)
    return {}


# A source checkout uses its own catalogs; the package installs them here.
_project_root = Path(__file__).resolve().parents[5]
_po_dirs = [Path("/usr/share/tts-biglinux/locale")]
if (_project_root / "locale" / "tts-biglinux.pot").is_file():
    _po_dirs.insert(0, _project_root / "locale")

try:
    locale.setlocale(locale.LC_ALL, "")
except locale.Error:
    logger.debug("Could not set locale, using default")

_translations: dict[str, str] = _load(_po_dirs, _locale_candidates())


def _(message: str) -> str:
    return _translations.get(message, message)


# ── Language and country names ───────────────────────────────────────
# Taken, already translated, from the system's iso-codes (a GTK dependency),
# so the catalogs do not carry ~100 language names.

_ISO_JSON = Path("/usr/share/iso-codes/json")
_SYSTEM_LOCALE = Path("/usr/share/locale")
_RE_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)$")  # "(macrolanguage)", "(1453-)"


@functools.cache
def _iso_names(standard: str, key: str) -> dict[str, str]:
    """iso-codes ``standard`` ("639-3", "3166-1"…): code → English name."""
    try:
        data = json.loads((_ISO_JSON / f"iso_{standard}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {e[key].lower(): e.get("common_name", e["name"]) for e in data.get(standard, []) if key in e}


@functools.cache
def _iso_translation(standard: str) -> gettext.NullTranslations:
    return gettext.translation(
        f"iso_{standard}", _SYSTEM_LOCALE, languages=_locale_candidates() or ["en"], fallback=True
    )


def language_name(code: str) -> str:
    """Name of a language ("pt", "pt-BR", "cmn"…) in the user's language."""
    code = code.lower().replace("_", "-").split("-")[0]
    key = "alpha_2" if len(code) == 2 else "alpha_3"
    english = ""
    for standard in ("639-3", "639-2"):
        name = _iso_names(standard, key).get(code)
        if not name:
            continue
        local = _iso_translation(standard).gettext(name)
        if local != name:
            return _RE_TRAILING_PAREN.sub("", local)
        english = english or name
    return _RE_TRAILING_PAREN.sub("", english) or code


def region_name(code: str) -> str:
    """Name of a country ("BR", "us"…) in the user's language."""
    name = _iso_names("3166-1", "alpha_2").get(code.lower())
    return _iso_translation("3166-1").gettext(name) if name else code.upper()


def language_with_region(language: str, region: str | None = None) -> str:
    """ "Portuguese (Brazil)"-style label; the language alone without a region."""
    if not region:
        return language_name(language)
    return _("{language} ({region})").format(language=language_name(language), region=region_name(region))
