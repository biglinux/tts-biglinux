#!/usr/bin/env python3
"""Regenerate locale/tts-biglinux.pot and bring every locale/*.po in line.

Strings are extracted exactly like BigLinux's translation pipeline does
(plain ``xgettext`` with the default Python keywords), so what this script
sees is what the pipeline translates. Existing translations are kept,
strings the code no longer uses are dropped and new ones get an empty msgstr.

The pipeline does not translate pt-BR: keep it complete by hand. New
translations can be merged from JSON files ({"English": "translation"}):

    python3 scripts/sync_translations.py [--merge pt-BR=new.json ...]

``--check`` changes nothing: it fails if a catalog cannot be read or a
translation uses a {placeholder} its source does not have (that would raise
KeyError at runtime). The translation workflow runs it before committing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "usr/share/biglinux/tts-biglinux"
LOCALE = ROOT / "locale"
POT = LOCALE / "tts-biglinux.pot"

sys.path.insert(0, str(APP))
from utils.i18n import _extract_string, _parse_po  # noqa: E402

_RE_BRACE = re.compile(r"\{[a-z_]+\}")


def extract() -> dict[str, list[str]]:
    """msgid → source references, in first-seen order."""
    files = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=APP, capture_output=True, text=True, check=True
    ).stdout.split()
    messages: dict[str, list[str]] = {}
    for name in sorted(files):
        out = subprocess.run(
            ["xgettext", "--from-code=UTF-8", "-o", "-", str(APP.relative_to(ROOT) / name)],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        refs: list[str] = []
        msgid: str | None = None
        for line in out.splitlines() + [""]:
            if line.startswith("#: "):
                refs += line[3:].split()
            elif line.startswith("msgid "):
                msgid = _extract_string(line[6:])
            elif line.startswith('"') and msgid is not None:
                msgid += _extract_string(line)
            elif line.startswith("msgstr"):
                if msgid:
                    messages.setdefault(msgid, []).extend(refs)
                refs, msgid = [], None
    return messages


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


def _field(keyword: str, text: str) -> str:
    if "\n" not in text.rstrip("\n"):
        return f"{keyword} {_quote(text)}\n"
    parts = text.splitlines(keepends=True)
    return f'{keyword} ""\n' + "".join(_quote(p) + "\n" for p in parts)


def _entries(messages: dict[str, list[str]], translations: dict[str, str]) -> str:
    out = []
    for msgid, refs in messages.items():
        block = "".join(f"#: {r}\n" for r in refs)
        if _RE_BRACE.search(msgid):
            block += "#, python-brace-format\n"
        block += _field("msgid", msgid) + _field("msgstr", translations.get(msgid, ""))
        out.append(block)
    return "\n".join(out)


def _header(path: Path) -> str:
    """Comments and the msgid "" header entry of an existing catalog."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("msgstr") and i and re.match(r'msgid\s+""\s*$', lines[i - 1]):
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith('"'):
                j += 1
            return "".join(lines[:j]).rstrip("\n") + "\n"
    raise SystemExit(f"{path}: no header entry")


def check() -> int:
    """Problems in the catalogs (0 = all good)."""
    problems = 0
    for po in sorted(LOCALE.glob("*.po")):
        try:
            catalog = _parse_po(po)
        except (OSError, UnicodeDecodeError) as e:
            print(f"{po.name}: unreadable ({e})")
            problems += 1
            continue
        for msgid, msgstr in catalog.items():
            extra = set(_RE_BRACE.findall(msgstr)) - set(_RE_BRACE.findall(msgid))
            if extra:
                print(f"{po.name}: {sorted(extra)} not in source: {msgid!r}")
                problems += 1
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--merge", action="append", default=[], metavar="LANG=FILE.json")
    parser.add_argument("--check", action="store_true", help="validate the catalogs, change nothing")
    args = parser.parse_args()
    if args.check:
        problems = check()
        print("catalogs OK" if not problems else f"{problems} problem(s)")
        sys.exit(1 if problems else 0)
    extra: dict[str, dict[str, str]] = {}
    for item in args.merge:
        lang, _sep, file = item.partition("=")
        extra[lang] = json.loads(Path(file).read_text(encoding="utf-8"))

    messages = extract()
    POT.write_text(_header(POT) + "\n" + _entries(messages, {}), encoding="utf-8")
    print(f"{POT.relative_to(ROOT)}: {len(messages)} strings")

    for po in sorted(LOCALE.glob("*.po")):
        lang = po.stem
        translations = {k: v for k, v in _parse_po(po).items() if k in messages}
        for msgid, msgstr in extra.get(lang, {}).items():
            if msgid in messages and msgstr:
                translations[msgid] = msgstr
        po.write_text(_header(po) + "\n" + _entries(messages, translations), encoding="utf-8")
        missing = len(messages) - len(translations)
        print(f"{po.relative_to(ROOT)}: {len(translations)} translated, {missing} missing")


if __name__ == "__main__":
    main()
