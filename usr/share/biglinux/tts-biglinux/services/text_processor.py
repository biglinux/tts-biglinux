"""
Text processor — clean, expand abbreviations, and prepare text for TTS.

Handles language-specific abbreviation expansion, special character
pronunciation, and text cleanup (markdown, HTML, etc.).
"""

from __future__ import annotations

import functools
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

    # ── Documents / Academic
    "art": "artigo",
    "cap": "capítulo",
    "pag": "página",
    "pág": "página",
    "vol": "volume",
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

# Titles and prefixes whose period is part of the abbreviation ("Dr. Silva",
# "Art. 5"): it is dropped with the expansion so it does not end a sentence.
_ABBREVIATION_TITLES = frozenset({
    "sr", "sra", "srta", "dr", "dra", "prof", "profa", "eng", "enga", "av",
    "art", "cap", "pag", "pág", "vol", "núm", "ref", "tel", "cel", "obs",
})

# Real words that are also in the slang list: never spelled out when
# "Expand abbreviations" is off.
_REAL_WORDS = frozenset({"tá", "tô", "eh", "ne", "add", "art", "cap", "vol", "info", "app", "apps"})

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

# A tag needs a letter right after "<": "x < 5 e y > 3" is not a tag.
_RE_HTML_TAGS = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
_RE_MARKDOWN_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
# Emphasis needs the marker against the word: "2 * 3 * 4" stays.
_RE_MARKDOWN_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])|(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)")
_RE_MARKDOWN_CODE = re.compile(r"`(.+?)`")
_RE_MARKDOWN_LINK = re.compile(r"\[(.+?)\]\(.+?\)")
_RE_MARKDOWN_HEADER = re.compile(r"^#{1,6}[ \t]+(.*?)[ \t]*#*[ \t]*$", re.MULTILINE)
_RE_MARKDOWN_LIST = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)
# "https://…" and "www.…", without the punctuation that ends the sentence.
_RE_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+?(?=[.,;:!?)\]]*(?:\s|$))")
_RE_URL_SPACED = re.compile(r"[ \t]*" + _RE_URL.pattern)
_RE_MULTI_SPACES = re.compile(r"[ \t\u00a0]{2,}")
_RE_SPACED_NEWLINE = re.compile(r"[ \t]*\n[ \t]*")
_RE_MULTI_NEWLINES = re.compile(r"\n{3,}")


def get_system_language() -> str:
    """The system language (ISO 639-1), by GNU gettext precedence."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value:
            code = value.split(".")[0].split("_")[0].lower()
            return "en" if code in ("c", "posix", "") else code
    return "en"


def _language_code(language: str | None) -> str:
    """"pt-BR", "pt_BR", "pt" → "pt"; None → the system language."""
    if not language:
        return get_system_language()
    return language.lower().replace("_", "-").split("-")[0]


def limit_text(text: str, max_chars: int) -> str:
    """At most ``max_chars`` characters, cut at a sentence or word boundary."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    floor = max_chars // 2  # never cut away more than half for a boundary
    sentence = max((cut.rfind(p) for p in (". ", "! ", "? ", "… ", "\n")), default=-1)
    if sentence >= floor:
        return cut[: sentence + 1].rstrip()
    space = cut.rfind(" ")
    if space >= floor:
        return cut[:space].rstrip()
    return cut


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
_RE_TIME_PT = re.compile(r"\b(\d{1,2})[:h](\d{2})(?::(\d{2}))?\b(?!:)")
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


def _hours_pt(h: int) -> str:
    """"uma hora", "duas horas", "vinte e uma horas" (hora is feminine)."""
    words = num_to_words_pt(h) or str(h)
    if h % 10 in (1, 2) and h not in (11, 12):
        words = words[: -len("um")] + "uma" if h % 10 == 1 else words[: -len("dois")] + "duas"
    return f"{words} {'hora' if h == 1 else 'horas'}"


def _time_repl_pt(m: re.Match) -> str:
    h, mnt = int(m.group(1)), int(m.group(2))
    sec = int(m.group(3)) if m.group(3) else None
    if not (0 <= h <= 23 and 0 <= mnt <= 59 and (sec is None or 0 <= sec <= 59)):
        return m.group(0)
    parts = [_hours_pt(h)]
    if mnt:
        parts.append(f"{num_to_words_pt(mnt)} {'minuto' if mnt == 1 else 'minutos'}")
    if sec:
        parts.append(f"{num_to_words_pt(sec)} {'segundo' if sec == 1 else 'segundos'}")
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " e " + parts[-1]


def _hour_repl_pt(m: re.Match) -> str:
    h = int(m.group(1))
    if not (0 <= h <= 23):
        return m.group(0)
    return _hours_pt(h)


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
        process_urls: Read web addresses as their domain (if False, remove them).
        strip_formatting: Remove markdown/HTML formatting.
        language: Language of the voice ("pt-BR", "en"…; system language if None).

    Returns:
        Cleaned text ready for TTS.
    """
    if not text or not text.strip():
        return ""

    lang = _language_code(language)

    # HTML entities are never meant to be read ("&amp;" → "&").
    if strip_formatting:
        text = _strip_formatting(text)
    text = html.unescape(text)

    # Web addresses: the domain, or nothing (with the space before them).
    if process_urls:
        text = _RE_URL.sub(_url_domain, text)
    else:
        text = _RE_URL_SPACED.sub("", text)

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
    else:
        # Leaving the symbols in is not "not reading" them: espeak-ng — and
        # Piper and Kokoro, which phonemize through it — says "arroba",
        # "mais", "igual", "asterisco"… on its own. Only RHVoice skips them.
        text = _remove_spoken_symbols(text)

    # Final cleanup: collapse spaces, keep paragraph breaks (pauses).
    text = _RE_MULTI_SPACES.sub(" ", text)
    text = _RE_SPACED_NEWLINE.sub("\n", text)
    text = _RE_MULTI_NEWLINES.sub("\n\n", text)
    return text.strip()


def _url_domain(m: re.Match) -> str:
    """"https://www.example.com/a?b=1" → "example.com"."""
    url = m.group(0)
    host = url.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.rsplit("@", 1)[-1].split(":", 1)[0]
    return host.removeprefix("www.")


def _strip_formatting(text: str) -> str:
    """Remove markdown and HTML formatting, keeping readable text.

    Tags go before entities are decoded, so "&lt;b&gt;" stays as text.
    Numbered lists keep their numbers: they are part of the content.
    """
    text = _RE_HTML_TAGS.sub("", text)
    text = _RE_MARKDOWN_LINK.sub(r"\1", text)
    text = _RE_MARKDOWN_BOLD.sub(r"\2", text)
    text = _RE_MARKDOWN_ITALIC.sub(lambda m: m.group(1) or m.group(2), text)
    text = _RE_MARKDOWN_CODE.sub(r"\1", text)
    # A heading becomes a sentence, so the engine pauses after it.
    text = _RE_MARKDOWN_HEADER.sub(
        lambda m: m.group(1) + ("" if not m.group(1) or m.group(1)[-1] in ".!?:…" else "."), text
    )
    return _RE_MARKDOWN_LIST.sub("", text)


@functools.cache
def _abbreviation_pattern(language: str) -> re.Pattern | None:
    """One regex for all abbreviations of ``language`` (longest first).

    An abbreviation is a whole token: not part of an e-mail, a URL, a path or
    a hyphenated word ("ex-presidente", "joao@tel.com", "app.config").
    """
    abbrevs = _ABBREVIATIONS.get(language)
    if not abbrevs:
        return None
    alternatives = "|".join(re.escape(a) for a in sorted(abbrevs, key=len, reverse=True))
    return re.compile(
        rf"(?<![\w@./\\-])(?P<abbr>{alternatives})(?P<dot>\.(?=\s))?(?![\w@/\\-]|\.\w)",
        re.IGNORECASE,
    )


def _expand_abbreviations(text: str, language: str) -> str:
    """Expand common abbreviations for the given language."""
    pattern = _abbreviation_pattern(language)
    if pattern is None:
        return text
    abbrevs = _ABBREVIATIONS[language]

    def repl(m: re.Match) -> str:
        abbr = m.group("abbr").lower()
        dot = m.group("dot") or ""
        return abbrevs[abbr] + ("" if abbr in _ABBREVIATION_TITLES else dot)

    return pattern.sub(repl, text)


def _bypass_internal_abbreviations(text: str, language: str) -> str:
    """Keep the engine from expanding slang itself (RHVoice reads "vc" as
    "você"): a zero-width space between the letters makes it spell them.
    Real words ("tá", "tô", "art") are left alone.
    """
    pattern = _abbreviation_pattern(language)
    if pattern is None:
        return text

    def repl(m: re.Match) -> str:
        abbr = m.group("abbr")
        if abbr.lower() in _REAL_WORDS or abbr.lower() in _ABBREVIATION_TITLES:
            return m.group(0)
        return "\u200b".join(abbr) + (m.group("dot") or "")

    return pattern.sub(repl, text)


# Symbols espeak-ng (and so Piper and Kokoro) turns into words, verified
# with `espeak-ng -x` and koko's phonemizer. "#" is silent there but read by
# other engines ("hashtag"). Pause-only marks — parentheses, brackets,
# quotes, dashes — stay: they shape the intonation.
_SPOKEN_SYMBOLS = frozenset("#@&=+*§¶·•©®™†‡→←↑↓↔⇒⇐※")
# Units and currency mean something next to a number ("50%", "25°",
# "R$ 10", "€5", "5 €"): kept there, removed elsewhere.
_NUMBER_SYMBOLS = frozenset("%°$€£¥¢")


def _remove_spoken_symbols(text: str) -> str:
    """Drop symbols the engines would read aloud (special chars off)."""
    out: list[str] = []
    n = len(text)
    for i, ch in enumerate(text):
        if ch in _SPOKEN_SYMBOLS:
            out.append(" ")
        elif ch in _NUMBER_SYMBOLS:
            j = i - 1
            while j >= 0 and text[j] == " ":
                j -= 1
            k = i + 1
            while k < n and text[k] == " ":
                k += 1
            next_is_digit = k < n and text[k].isdigit()
            prev_is_digit = j >= 0 and text[j].isdigit()
            out.append(ch if (prev_is_digit or next_is_digit) else " ")
        else:
            out.append(ch)
    return "".join(out)


def _process_special_chars(text: str, language: str) -> str:
    """Replace special characters with their spoken form.

    Languages without a table keep the symbols: the engine reads them in the
    voice's own language (better than English words in a German text).
    """
    chars = _SPECIAL_CHARS.get(language, {})
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
