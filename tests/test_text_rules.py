"""Text rules found wrong by the 4.1.0 audit (each case was reproduced)."""
import pytest

import html

from services.text_processor import _strip_formatting, get_system_language, limit_text, process_text


def pt(text: str, **kw) -> str:
    return process_text(text, language="pt-BR", **kw)


def en(text: str, **kw) -> str:
    return process_text(text, language="en-US", **kw)


# ── The voice's language decides, not the system's ────────────────────

def test_english_voice_on_a_portuguese_system(monkeypatch):
    monkeypatch.delenv("LC_ALL", raising=False)  # builders: LC_ALL=C
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    out = en("Mail john@example.com, 50% off at 5 p.m. with Dr. Jones")
    assert "arroba" not in out and "cinquenta" not in out and "doutor" not in out


def test_system_language_follows_gettext_precedence(monkeypatch):
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    assert get_system_language() == "de"
    monkeypatch.setenv("LC_ALL", "C")
    assert get_system_language() == "en"


def test_languages_without_a_table_keep_symbols_for_the_engine():
    assert "@" in process_text("a@b", language="de", process_special_chars=True)


# ── Abbreviations ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("o ex-presidente falou", "o ex-presidente falou"),
        ("escreva para joao@ex.com", "escreva para joao ex.com"),  # "@" off: removed
        ("tome vitamina C", "tome vitamina C"),
        ("a empresa S.A. lucrou", "a empresa S.A. lucrou"),
        ("João P. Silva chegou", "João P. Silva chegou"),
        ("num lugar distante", "num lugar distante"),
        ("o Dr. Silva chegou", "o doutor Silva chegou"),
        ("vc vem hj?", "você vem hoje?"),
        ("Tudo certo. Att.", "Tudo certo. atenciosamente."),
    ],
)
def test_portuguese_abbreviations(text, expected):
    assert pt(text, process_special_chars=False) == expected


def test_english_slash_abbreviations():
    assert en("coffee w/o sugar") == "coffee without sugar"
    assert en("tea w/ milk") == "tea with milk"


def test_abbreviations_off_never_spell_real_words():
    out = pt("Ele tá aqui e eu tô lá, vc sabe", expand_abbreviations=False)
    assert "tá" in out and "tô" in out
    assert "v\u200bc" in out  # slang is spelled, not expanded by the engine


# ── Hours are feminine in Portuguese ─────────────────────────────────

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1:30", "uma hora e trinta minutos"),
        ("2h", "duas horas"),
        ("21:00", "vinte e uma horas"),
        ("22h15", "vinte e duas horas e quinze minutos"),
        ("12:00", "doze horas"),
        ("11h", "onze horas"),
        ("10:30:45", "dez horas, trinta minutos e quarenta e cinco segundos"),
    ],
)
def test_hours(text, expected):
    assert pt(text) == expected


# ── Remove formatting ────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("se x < 5 e y > 3", "se x < 5 e y > 3"),
        ("1984. Foi um ano", "1984. Foi um ano"),
        ("2 * 3 * 4", "2 * 3 * 4"),
        ("**negrito** e _itálico_ e *ênfase*", "negrito e itálico e ênfase"),
        ("<b>forte</b> &lt;tag&gt;", "forte <tag>"),
        ("nome_de_arquivo", "nome_de_arquivo"),
    ],
)
def test_strip_formatting(text, expected):
    assert html.unescape(_strip_formatting(text)) == expected


def test_entities_are_decoded_even_without_strip_formatting():
    out = pt("A &amp; B", strip_formatting=False, process_special_chars=False)
    assert "amp" not in out


def test_headings_keep_a_pause_and_paragraphs_survive():
    out = pt("# Título\n\nPrimeiro parágrafo.\n\nSegundo.", normalize_numbers=False)
    assert out == "Título.\n\nPrimeiro parágrafo.\n\nSegundo."


# ── Web addresses ────────────────────────────────────────────────────

def test_urls_off_removes_www_and_keeps_the_final_period():
    out = pt("Visite www.exemplo.com.br. Depois https://a.com/x?y=1.", process_special_chars=False)
    assert out == "Visite. Depois."


def test_urls_on_reads_the_domain():
    out = pt("Veja https://www.example.com/a?b=1 agora", process_urls=True, process_special_chars=False)
    assert out == "Veja example.com agora"


# ── Character limit ──────────────────────────────────────────────────

def test_limit_cuts_at_a_sentence_boundary():
    text = "Primeira frase completa. Segunda frase que passa do limite."
    assert limit_text(text, 40) == "Primeira frase completa."


def test_limit_cuts_at_a_word_boundary():
    text = "palavra " * 20
    out = limit_text(text, 50)
    assert len(out) <= 50 and not out.endswith("palav")
    assert out.split()[-1] == "palavra"


def test_limit_zero_is_unlimited():
    assert limit_text("x" * 10_000, 0) == "x" * 10_000
