"""'Read special characters' off must mean silence on every engine.

RHVoice skips most symbols by itself; espeak-ng — and Piper and Kokoro,
which phonemize through it — reads "@", "&", "=", "+", "*", "§", "©", "→"
aloud. With the option off they are removed before synthesis.
"""
import importlib
import shutil
import subprocess

import pytest

tp = importlib.import_module("services.text_processor")


def off(text):
    return tp.process_text(
        text, process_special_chars=False, normalize_numbers=False,
        expand_abbreviations=False, language="pt",
    )


@pytest.mark.parametrize("symbol", list("#@&=+*§©®™→·"))
def test_symbols_read_as_words_are_removed(symbol):
    assert symbol not in off(f"casa {symbol} casa")
    assert off(f"casa {symbol} casa") == "casa casa"


@pytest.mark.parametrize("text", ["Desconto de 50% hoje", "Faz 25° lá fora", "Custa R$ 10", "Custa €5", "Custa 5 €"])
def test_units_next_to_numbers_are_kept(text):
    assert off(text) == text


def test_stray_units_are_removed():
    assert off("o % e o ° e o $ sozinhos") == "o e o e o sozinhos"


def test_pause_marks_stay():
    text = 'Ele disse (baixinho) "tudo bem" — e saiu'
    assert off(text) == text


def test_option_on_still_spells_symbols():
    on = tp.process_text("a @ b", process_special_chars=True, language="pt")
    assert "arroba" in on


@pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="espeak-ng not installed")
def test_espeak_says_no_symbol_words_when_off():
    raw = "joao@site.com & #BigLinux; 2 + 2 = 4 * fator; § 3 © 2026 → fim"

    def phonemes(text):
        return subprocess.run(
            ["espeak-ng", "-v", "pt-br", "-q", "-x", text],
            capture_output=True, text=True, timeout=30,
        ).stdout

    words = ["ax'ob", "igw'&l", "ste*'isk", "ses'&U", "s'imbolU", "s'Et&"]
    assert any(w in phonemes(raw) for w in words)  # the engine does read them
    assert not any(w in phonemes(off(raw)) for w in words)
