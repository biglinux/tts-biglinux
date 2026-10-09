"""Translations: catalog parsing, language selection, catalog coverage."""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import utils.i18n as i18n

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "usr" / "share" / "biglinux" / "tts-biglinux"
LOCALE = ROOT / "locale"


def test_development_locale_directory_points_to_repository() -> None:
    """The source checkout must win over a system-installed catalog."""
    assert i18n._po_dirs[0] == LOCALE


# ── Parser ───────────────────────────────────────────────────────────


def _po(tmp_path: Path, body: str) -> dict[str, str]:
    path = tmp_path / "x.po"
    path.write_text('msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n' + body, encoding="utf-8")
    return i18n._parse_po(path)


def test_escapes_are_decoded_in_one_pass(tmp_path):
    tr = _po(tmp_path, 'msgid "C:\\\\new\\tTab \\"q\\"\\n"\nmsgstr "ok"\n')
    assert tr == {'C:\\new\tTab "q"\n': "ok"}


def test_multiline_and_indented_continuations(tmp_path):
    tr = _po(tmp_path, 'msgid  ""\n        "Hello "\n        "world"\nmsgstr ""\n"Olá "\n"mundo"\n')
    assert tr == {"Hello world": "Olá mundo"}


def test_entries_without_blank_lines_between_them(tmp_path):
    tr = _po(tmp_path, '#\n# File: a\nmsgid  "One"\nmsgstr "Um"\n#\n# File: b\nmsgid  "Two"\nmsgstr "Dois"\n')
    assert tr == {"One": "Um", "Two": "Dois"}


def test_fuzzy_context_plural_and_obsolete_entries_are_skipped(tmp_path):
    tr = _po(
        tmp_path,
        '#, fuzzy\nmsgid "Fuzzy"\nmsgstr "Difuso"\n\n'
        'msgctxt "verb"\nmsgid "Open"\nmsgstr "Abrir"\n\n'
        'msgid "Open"\nmsgstr "Aberto"\n\n'
        'msgid "{n} file"\nmsgid_plural "{n} files"\nmsgstr[0] "{n} arquivo"\nmsgstr[1] "{n} arquivos"\n\n'
        '#~ msgid "Old"\n#~ msgstr "Velho"\n\n'
        'msgid "Empty"\nmsgstr ""\n\n'
        '#, python-brace-format\nmsgid "Kept {x}"\nmsgstr "Mantido {x}"\n',
    )
    assert tr == {"Open": "Aberto", "Kept {x}": "Mantido {x}"}


# ── Language selection (GNU gettext precedence) ──────────────────────


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"LANG": "pt_BR.UTF-8"}, ["pt_BR", "pt"]),
        ({"LANG": "pt_PT.UTF-8"}, ["pt_PT", "pt"]),
        ({"LANG": "de_AT.UTF-8"}, ["de_AT", "de"]),
        ({"LANG": "nb_NO.UTF-8"}, ["nb_NO", "nb", "no"]),
        ({"LANG": "C"}, []),
        ({"LANG": "C.UTF-8", "LANGUAGE": "pt_BR"}, []),
        ({"LC_ALL": "C", "LANG": "pt_BR.UTF-8"}, []),
        ({"LC_ALL": "de_DE.UTF-8", "LANG": "pt_BR.UTF-8"}, ["de_DE", "de"]),
        ({"LC_MESSAGES": "fr_FR.UTF-8", "LANG": "pt_BR.UTF-8"}, ["fr_FR", "fr"]),
        ({"LANG": "en_US.UTF-8", "LANGUAGE": "fr:pt_BR"}, ["fr", "pt_BR", "pt"]),
        ({"LANG": "pt-BR"}, ["pt_BR", "pt"]),
        ({}, []),
    ],
)
def test_locale_candidates(env, expected):
    assert i18n._locale_candidates(env) == expected


def test_catalog_lookup_is_case_insensitive(tmp_path):
    (tmp_path / "pt-BR.po").write_text("")
    (tmp_path / "pt.po").write_text("")
    found = i18n._find_catalogs(tmp_path, ["pt_br", "pt"])
    assert [p.name for p in found] == ["pt-BR.po", "pt.po"]


def test_each_message_falls_back_through_the_language_list(tmp_path):
    (tmp_path / "fr.po").write_text('msgid "A"\nmsgstr "A-fr"\n', encoding="utf-8")
    (tmp_path / "pt-BR.po").write_text('msgid "A"\nmsgstr "A-br"\nmsgid "B"\nmsgstr "B-br"\n', encoding="utf-8")
    tr = i18n._load([tmp_path], ["fr", "pt_BR", "pt"])
    assert tr == {"A": "A-fr", "B": "B-br"}


def _translate_in(env: dict[str, str], message: str) -> str:
    clean = {k: v for k, v in os.environ.items() if k not in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")}
    out = subprocess.run(
        [sys.executable, "-c", f"from utils.i18n import _; print(_({message!r}))"],
        cwd=APP, env={**clean, **env, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"LANG": "pt_BR.UTF-8"}, "Pronto para falar"),
        ({"LANG": "pt_PT.UTF-8"}, "Pronto para falar"),
        ({"LANG": "de_DE.UTF-8"}, "Bereit zum Sprechen"),
        ({"LC_ALL": "C", "LANG": "pt_BR.UTF-8"}, "Ready to speak"),
        ({"LANG": "en_US.UTF-8"}, "Ready to speak"),
    ],
)
def test_real_catalogs_follow_the_system_language(env, expected):
    assert _translate_in(env, "Ready to speak") == expected


# ── Catalog coverage ─────────────────────────────────────────────────


def _code_messages() -> dict[str, str]:
    """Every literal passed to _() in the application → file:line."""
    found: dict[str, str] = {}
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_":
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.setdefault(arg.value, f"{path.relative_to(APP)}:{node.lineno}")
    return found


def test_every_translated_string_is_a_literal_xgettext_can_extract():
    # BigLinux's pipeline runs plain xgettext: _(variable) and N_() markers are
    # invisible to it, and their strings vanish from every catalog.
    bad = []
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "N_":
                    bad.append(f"{path.relative_to(APP)}:{node.lineno} N_()")
                elif node.func.id == "_":
                    arg = node.args[0] if node.args else None
                    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                        bad.append(f"{path.relative_to(APP)}:{node.lineno} _(non-literal)")
            # Nor does it look inside f-strings: f"{_('x')}" is lost too.
            if isinstance(node, ast.JoinedStr):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "_":
                        bad.append(f"{path.relative_to(APP)}:{inner.lineno} _() inside an f-string")
    assert not bad, bad


@pytest.mark.parametrize("lang", ["pt-BR", "pt"])
def test_portuguese_catalogs_are_complete(lang):
    catalog = i18n._parse_po(LOCALE / f"{lang}.po")
    missing = {m: where for m, where in _code_messages().items() if m not in catalog}
    assert not missing, missing


def test_pot_lists_every_string(tmp_path):
    # The template's msgstr are empty; give them a value so the parser keeps them.
    text = (LOCALE / "tts-biglinux.pot").read_text(encoding="utf-8")
    filled = tmp_path / "filled.po"
    filled.write_text(re.sub(r'^msgstr\s+""$', 'msgstr "x"', text, flags=re.M), encoding="utf-8")
    pot = i18n._parse_po(filled)
    missing = {m: where for m, where in _code_messages().items() if m not in pot}
    assert not missing, missing
