"""
Text processor — clean, expand abbreviations, and prepare text for TTS.

Handles language-specific abbreviation expansion, special character
pronunciation, and text cleanup (markdown, HTML, etc.).
"""

from __future__ import annotations

import html
import logging
import os
import re

logger = logging.getLogger(__name__)

# ── Abbreviation Dictionaries ────────────────────────────────────────

ABBREVIATIONS_PT: dict[str, str] = {
    "tb": "também",
    "tbm": "também",
    "vc": "você",
    "vcs": "vocês",
    "td": "tudo",
    "pq": "porque",
    "hj": "hoje",
    "mt": "muito",
    "mto": "muito",
    "qd": "quando",
    "qdo": "quando",
    "oq": "o que",
    "dps": "depois",
    "vlw": "valeu",
    "blz": "beleza",
    "msg": "mensagem",
    "msgs": "mensagens",
    "obg": "obrigado",
    "obgd": "obrigado",
    "obgda": "obrigada",
    "cmg": "comigo",
    "ctg": "contigo",
    "bjs": "beijos",
    "abs": "abraços",
    "qnt": "quanto",
    "msm": "mesmo",
    "ngm": "ninguém",
    "tmb": "também",
    "flw": "falou",
    "pfv": "por favor",
    "pf": "por favor",
    "fds": "fim de semana",
    "nd": "nada",
    "ctz": "certeza",
    "rsrs": "risos",
    "kk": "risos",
    "kkk": "risos",
    "kkkk": "risos",
    "sq": "só que",
    "sla": "sei lá",
    "agr": "agora",
    "dnd": "de nada",
    "dnv": "de novo",
    "q": "que",
    "p": "para",
    "c": "com",
    "n": "não",
    "s": "sim",
    "eh": "é",
    "ne": "né",
    "tô": "estou",
    "tá": "está",
    "vdd": "verdade",
    "add": "adicionar",
    "ref": "referência",
    "info": "informação",
    "infos": "informações",
    "config": "configuração",
    "configs": "configurações",
    "app": "aplicativo",
    "apps": "aplicativos",
}

ABBREVIATIONS_EN: dict[str, str] = {
    "btw": "by the way",
    "idk": "I don't know",
    "imo": "in my opinion",
    "imho": "in my humble opinion",
    "fyi": "for your information",
    "tbh": "to be honest",
    "afaik": "as far as I know",
    "lol": "laughing out loud",
    "omg": "oh my god",
    "brb": "be right back",
    "ttyl": "talk to you later",
    "nvm": "never mind",
    "thx": "thanks",
    "ty": "thank you",
    "np": "no problem",
    "pls": "please",
    "plz": "please",
    "rn": "right now",
    "w/": "with",
    "w/o": "without",
    "info": "information",
    "config": "configuration",
    "app": "application",
    "apps": "applications",
    "govt": "government",
    "dept": "department",
    "mgmt": "management",
    "approx": "approximately",
    "misc": "miscellaneous",
}

ABBREVIATIONS_ES: dict[str, str] = {
    "tb": "también",
    "xq": "porque",
    "pq": "porque",
    "x": "por",
    "q": "que",
    "d": "de",
    "dnd": "de nada",
    "grax": "gracias",
    "msj": "mensaje",
    "tmb": "también",
    "xfa": "por favor",
}

# ── Special Character Pronunciations ─────────────────────────────────

SPECIAL_CHARS_PT: dict[str, str] = {
    "#": " cerquilha ",
    "@": " arroba ",
    "%": " por cento ",
    "/": " barra ",
    " - ": " traço ",
    "&": " e comercial ",
    "=": " igual ",
    "+": " mais ",
    "*": " asterisco ",
    "~": " til ",
    "^": " circunflexo ",
    "|": " barra vertical ",
    "\\": " barra invertida ",
    "<": " menor que ",
    ">": " maior que ",
    "{": " abre chaves ",
    "}": " fecha chaves ",
    "[": " abre colchetes ",
    "]": " fecha colchetes ",
    "(": " abre parênteses ",
    ")": " fecha parênteses ",
}

SPECIAL_CHARS_EN: dict[str, str] = {
    "#": " hash ",
    "@": " at ",
    "%": " percent ",
    "/": " slash ",
    " - ": " dash ",
    "&": " ampersand ",
    "=": " equals ",
    "+": " plus ",
    "*": " asterisk ",
    "~": " tilde ",
    "^": " caret ",
    "|": " pipe ",
    "\\": " backslash ",
    "<": " less than ",
    ">": " greater than ",
    "{": " open brace ",
    "}": " close brace ",
    "[": " open bracket ",
    "]": " close bracket ",
    "(": " open paren ",
    ")": " close paren ",
}

SPECIAL_CHARS_ES: dict[str, str] = {
    "#": " almohadilla ",
    "@": " arroba ",
    "%": " por ciento ",
    "/": " barra ",
    " - ": " guión ",
    "&": " y comercial ",
}

# ── Language Mapping ─────────────────────────────────────────────────

_ABBREVIATIONS: dict[str, dict[str, str]] = {
    "pt": ABBREVIATIONS_PT,
    "en": ABBREVIATIONS_EN,
    "es": ABBREVIATIONS_ES,
}

_SPECIAL_CHARS: dict[str, dict[str, str]] = {
    "pt": SPECIAL_CHARS_PT,
    "en": SPECIAL_CHARS_EN,
    "es": SPECIAL_CHARS_ES,
}

# ── Regex Patterns ───────────────────────────────────────────────────

_RE_HTML_TAGS = re.compile(r"<[^>]+>")
_RE_MARKDOWN_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RE_MARKDOWN_ITALIC = re.compile(r"\*(.+?)\*")
_RE_MARKDOWN_CODE = re.compile(r"`(.+?)`")
_RE_MARKDOWN_LINK = re.compile(r"\[(.+?)\]\(.+?\)")
_RE_MARKDOWN_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_RE_MARKDOWN_LIST = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_RE_MARKDOWN_ORDERED = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_RE_URL = re.compile(r"https?://\S+")
_RE_MULTI_SPACES = re.compile(r"\s{2,}")
_RE_MULTI_NEWLINES = re.compile(r"\n{3,}")


def get_system_language() -> str:
    """Get system language code (2-letter ISO 639-1)."""
    lang = os.environ.get("LANG", "en_US.UTF-8")
    return lang[:2].lower()


def process_text(
    text: str,
    *,
    expand_abbreviations: bool = True,
    process_special_chars: bool = True,
    process_urls: bool = False,
    strip_formatting: bool = True,
    language: str | None = None,
) -> str:
    """
    Process text for TTS reading.

    Args:
        text: Raw text to process.
        expand_abbreviations: Replace common abbreviations.
        process_special_chars: Read special characters aloud.
        process_urls: Read URL domains (if False, removes URLs).
        strip_formatting: Remove markdown/HTML formatting.
        language: Language code (auto-detect if None).

    Returns:
        Cleaned text ready for TTS.
    """
    if not text or not text.strip():
        return ""

    lang = language or get_system_language()

    # Strip formatting first
    if strip_formatting:
        text = _strip_formatting(text)

    # Handle URLs
    if not process_urls:
        text = _RE_URL.sub("", text)

    # Expand abbreviations
    if expand_abbreviations:
        text = _expand_abbreviations(text, lang)

    # Process special characters
    if process_special_chars:
        text = _process_special_chars(text, lang)

    # Final cleanup
    text = _RE_MULTI_SPACES.sub(" ", text)
    text = _RE_MULTI_NEWLINES.sub("\n\n", text)
    text = text.strip()

    return text


def _strip_formatting(text: str) -> str:
    """Remove markdown and HTML formatting, keeping readable text."""
    # HTML
    text = html.unescape(text)
    text = _RE_HTML_TAGS.sub("", text)

    # Markdown — preserve content, remove syntax
    text = _RE_MARKDOWN_LINK.sub(r"\1", text)
    text = _RE_MARKDOWN_BOLD.sub(r"\1", text)
    text = _RE_MARKDOWN_ITALIC.sub(r"\1", text)
    text = _RE_MARKDOWN_CODE.sub(r"\1", text)
    text = _RE_MARKDOWN_HEADER.sub("", text)
    text = _RE_MARKDOWN_LIST.sub("", text)
    text = _RE_MARKDOWN_ORDERED.sub("", text)

    return text


def _expand_abbreviations(text: str, language: str) -> str:
    """Expand common abbreviations for the given language."""
    abbrevs = _ABBREVIATIONS.get(language, {})
    if not abbrevs:
        return text

    for abbr, expansion in abbrevs.items():
        # Word-boundary matching, case-insensitive
        pattern = re.compile(r"\b" + re.escape(abbr) + r"\b", re.IGNORECASE)
        text = pattern.sub(expansion, text)

    return text


def _process_special_chars(text: str, language: str) -> str:
    """Replace special characters with their spoken form."""
    chars = _SPECIAL_CHARS.get(language, _SPECIAL_CHARS.get("en", {}))
    for char, spoken in chars.items():
        text = text.replace(char, spoken)
    return text
