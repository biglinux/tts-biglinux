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
    # ── Internet slang
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
    
    # ── Standard / Professional
    "sr": "senhor",
    "sra": "senhora",
    "srta": "senhorita",
    "dr": "doutor",
    "dra": "doutora",
    "prof": "professor",
    "profa": "professora",
    "eng": "engenheiro",
    "enga": "engenheira",
    "av": "avenida",
    "cia": "companhia",
    "ltda": "limitada",
    "tel": "telefone",
    "cel": "celular",
    "att": "atenciosamente",
    "obs": "observação",
    "ex": "exemplo",
    
    # ── Documents / Academic
    "art": "artigo",
    "cap": "capítulo",
    "ed": "edição",
    "pag": "página",
    "pág": "página",
    "vol": "volume",
    "num": "número",
    "núm": "número",
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


# ── Portuguese number normalization ──────────────────────────────────
#
# TTS engines read raw digits inconsistently, and pt-BR formatting
# ("1.250,90") confuses them (dot = thousands, comma = decimal). We convert
# currency, percentages and separator-formatted numbers into spoken words so
# "R$ 1.250,90" is read as "mil duzentos e cinquenta reais e noventa centavos".

_UNIDADES_PT = [
    "zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito",
    "nove", "dez", "onze", "doze", "treze", "catorze", "quinze", "dezesseis",
    "dezessete", "dezoito", "dezenove",
]
_DEZENAS_PT = [
    "", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta",
    "oitenta", "noventa",
]
_CENTENAS_PT = [
    "", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos",
    "seiscentos", "setecentos", "oitocentos", "novecentos",
]
# Scale word per 1000-group index: index 1 = mil, 2 = milhão, ...
_ESCALAS_PT = [
    ("", ""), ("mil", "mil"), ("milhão", "milhões"), ("bilhão", "bilhões"),
    ("trilhão", "trilhões"),
]


def _tres_pt(n: int) -> str:
    """Spell an integer 0..999 in Portuguese (no leading/trailing spaces)."""
    if n == 0:
        return ""
    if n == 100:
        return "cem"
    parts: list[str] = []
    centena, resto = divmod(n, 100)
    if centena:
        parts.append(_CENTENAS_PT[centena])
    if resto:
        if resto < 20:
            parts.append(_UNIDADES_PT[resto])
        else:
            dezena, unidade = divmod(resto, 10)
            if unidade:
                parts.append(f"{_DEZENAS_PT[dezena]} e {_UNIDADES_PT[unidade]}")
            else:
                parts.append(_DEZENAS_PT[dezena])
    return " e ".join(parts)


def num_to_words_pt(n: int) -> str:
    """Spell a non-negative integer (0..10^15) in Brazilian Portuguese.

    Applies the standard "e" conjunction rules, e.g.
    1250 → "mil duzentos e cinquenta", 2500 → "dois mil e quinhentos".
    """
    n = int(n)
    if n < 0:
        return "menos " + num_to_words_pt(-n)
    if n == 0:
        return "zero"

    grupos: list[int] = []
    while n > 0:
        grupos.append(n % 1000)
        n //= 1000
    if len(grupos) > len(_ESCALAS_PT):
        return None  # out of supported range — leave the digits untouched

    # Build most-significant-first.
    parts: list[str] = []
    vals: list[int] = []
    for idx in range(len(grupos) - 1, -1, -1):
        g = grupos[idx]
        if g == 0:
            continue
        if idx == 0:
            txt = _tres_pt(g)
        elif idx == 1:
            txt = "mil" if g == 1 else f"{_tres_pt(g)} mil"
        else:
            sing, plur = _ESCALAS_PT[idx]
            txt = f"{_tres_pt(g)} {sing if g == 1 else plur}"
        parts.append(txt)
        vals.append(g)

    result = parts[0]
    for k in range(1, len(parts)):
        g = vals[k]
        # "e" before the final group when it is < 100 or a round hundred.
        if k == len(parts) - 1 and (g < 100 or g % 100 == 0):
            result += f" e {parts[k]}"
        else:
            result += f" {parts[k]}"
    return result


_RE_CURRENCY_PT = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{1,2}))?")
_RE_PERCENT_PT = re.compile(r"(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d+))?\s*%")
_RE_NUMBER_PT = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+),(\d+)|(?<![\d.,/-])(\d{1,3}(?:\.\d{3})+)(?![\d.,])")


def _int_from_grouped(s: str) -> int:
    """Parse a pt-BR grouped integer string ('1.250') to int (1250)."""
    return int(s.replace(".", ""))


def _decimal_digits_pt(frac: str) -> str:
    """Spell the fractional part digit-by-digit ('90' → 'noventa'? no — read
    as a whole small number when 1-2 digits, else digit by digit)."""
    if len(frac) <= 2:
        return num_to_words_pt(int(frac))
    return " ".join(_UNIDADES_PT[int(d)] for d in frac)


def _currency_repl_pt(m: re.Match) -> str:
    reais = _int_from_grouped(m.group(1))
    centavos = int(m.group(2)) if m.group(2) else 0
    out = []
    reais_words = num_to_words_pt(reais)
    if reais_words is None:
        return m.group(0)
    out.append(f"{reais_words} {'real' if reais == 1 else 'reais'}")
    if centavos:
        cent_words = num_to_words_pt(centavos)
        out.append(f"{cent_words} {'centavo' if centavos == 1 else 'centavos'}")
    return " e ".join(out)


def _percent_repl_pt(m: re.Match) -> str:
    inteiro = _int_from_grouped(m.group(1))
    words = num_to_words_pt(inteiro)
    if words is None:
        return m.group(0)
    if m.group(2):
        words += " vírgula " + _decimal_digits_pt(m.group(2))
    return f"{words} por cento"


def _number_repl_pt(m: re.Match) -> str:
    # Two alternatives: grouped-with-decimal, or grouped integer.
    if m.group(1) is not None:  # has decimal
        inteiro = _int_from_grouped(m.group(1))
        words = num_to_words_pt(inteiro)
        if words is None:
            return m.group(0)
        return words + " vírgula " + _decimal_digits_pt(m.group(2))
    inteiro = _int_from_grouped(m.group(3))
    words = num_to_words_pt(inteiro)
    return words if words is not None else m.group(0)


_MESES_PT = [
    "", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
    "agosto", "setembro", "outubro", "novembro", "dezembro",
]
_RE_DATE_PT = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_RE_TIME_PT = re.compile(r"\b(\d{1,2})[:h](\d{2})\b")
_RE_HOUR_PT = re.compile(r"\b(\d{1,2})h\b")


def _date_repl_pt(m: re.Match) -> str:
    dia, mes, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= dia <= 31 and 1 <= mes <= 12):
        return m.group(0)
    dia_w = "primeiro" if dia == 1 else num_to_words_pt(dia)
    ano_w = num_to_words_pt(ano)
    if dia_w is None or ano_w is None:
        return m.group(0)
    return f"{dia_w} de {_MESES_PT[mes]} de {ano_w}"


def _time_repl_pt(m: re.Match) -> str:
    h, mnt = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mnt <= 59):
        return m.group(0)
    h_w = num_to_words_pt(h)
    out = f"{h_w} {'hora' if h == 1 else 'horas'}"
    if mnt:
        m_w = num_to_words_pt(mnt)
        out += f" e {m_w} {'minuto' if mnt == 1 else 'minutos'}"
    return out


def _hour_repl_pt(m: re.Match) -> str:
    h = int(m.group(1))
    if not (0 <= h <= 23):
        return m.group(0)
    return f"{num_to_words_pt(h)} {'hora' if h == 1 else 'horas'}"


def _normalize_numbers_pt(text: str) -> str:
    """Normalize pt-BR dates, times, currency, percentages and numbers to words."""
    # Dates and times first, so their digits aren't consumed by number rules.
    text = _RE_DATE_PT.sub(_date_repl_pt, text)
    text = _RE_TIME_PT.sub(_time_repl_pt, text)
    text = _RE_HOUR_PT.sub(_hour_repl_pt, text)
    text = _RE_CURRENCY_PT.sub(_currency_repl_pt, text)
    text = _RE_PERCENT_PT.sub(_percent_repl_pt, text)
    text = _RE_NUMBER_PT.sub(_number_repl_pt, text)
    return text


def process_text(
    text: str,
    *,
    expand_abbreviations: bool = True,
    process_special_chars: bool = True,
    process_urls: bool = False,
    strip_formatting: bool = True,
    normalize_numbers: bool = True,
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

    # Normalize numbers/currency/percent (pt only) BEFORE special chars so the
    # "%", "R$" and separators are turned into words rather than symbol names.
    if normalize_numbers and lang == "pt":
        text = _normalize_numbers_pt(text)

    # Expand abbreviations
    if expand_abbreviations:
        text = _expand_abbreviations(text, lang)
    else:
        # If disabled, prevent the TTS engine's internal phonemizer from
        # auto-expanding it anyway (like RHVoice's Letícia does for 'vc').
        text = _bypass_internal_abbreviations(text, lang)

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


def _bypass_internal_abbreviations(text: str, language: str) -> str:
    """
    Prevent the TTS engine itself from doing unwanted internal abbreviation
    expansion (common in RHVoice and Piper). Injects a zero-width space
    between the abbreviation characters to force literal sequential reading.
    """
    abbrevs = _ABBREVIATIONS.get(language, {})
    if not abbrevs:
        return text

    for abbr in abbrevs.keys():
        # Word-boundary matching, case-insensitive
        pattern = re.compile(r"\b" + re.escape(abbr) + r"\b", re.IGNORECASE)
        # Keep original case by using a lambda to insert \u200b
        text = pattern.sub(lambda m: "\u200b".join(m.group(0)), text)

    return text


def _process_special_chars(text: str, language: str) -> str:
    """Replace special characters with their spoken form."""
    chars = _SPECIAL_CHARS.get(language, _SPECIAL_CHARS.get("en", {}))
    for char, spoken in chars.items():
        text = text.replace(char, spoken)
    return text


# ── Chunking for long texts ──────────────────────────────────────────

_RE_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n{2,}")


def chunk_text(text: str, max_chars: int = 2000) -> list[str]:
    """Split text into chunks of at most `max_chars`, preserving semantics.

    Prefers sentence/paragraph boundaries; falls back to word boundaries and,
    only as a last resort, a hard cut (for pathological inputs with no spaces).
    Enables streaming synthesis (synthesize/play one chunk while preparing the
    next) so very long texts don't go to the engine as a single huge block.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Split into sentence-ish units first.
    units: list[str] = []
    for part in _RE_SENTENCE_SPLIT.split(text):
        part = part.strip()
        if part:
            units.append(part)
    if not units:
        units = [text]

    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > max_chars:
            # Flush current, then break the oversized unit on word boundaries.
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_unit(unit, max_chars))
            continue
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= max_chars:
            current = f"{current} {unit}"
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def _split_long_unit(unit: str, max_chars: int) -> list[str]:
    """Break a single oversized unit on word boundaries (hard cut if needed)."""
    out: list[str] = []
    current = ""
    for word in unit.split(" "):
        if len(word) > max_chars:
            if current:
                out.append(current)
                current = ""
            # Hard cut a pathologically long token.
            for i in range(0, len(word), max_chars):
                out.append(word[i:i + max_chars])
            continue
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= max_chars:
            current = f"{current} {word}"
        else:
            out.append(current)
            current = word
    if current:
        out.append(current)
    return out
