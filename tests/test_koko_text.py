"""Text sent to `koko pipe` must be readable to the end.

Real failures reproduced with koko (kokorox v0.2.2):
- "Espere... isso" / "Sério?! Não" → exit 1 (SendError): koko cuts at each
  sign and chokes on the punctuation-only piece in between;
- a sentence longer than the model's 510 phoneme tokens → panic
  ("index out of bounds: the len is 511 but the index is 550").
"""
import importlib
import re

import pytest

kvs = importlib.import_module("services.kokoro_voice_service")
prep = kvs.prepare_koko_text


def _words(text):
    return [w.strip(".,!?…;:") for w in text.split() if w.strip(".,!?…;:")]


@pytest.mark.parametrize(
    "raw",
    ["Espere... isso é um teste... certo?", "Sério?! Não acredito.", "Ok!? Sim.",
     "Será…? Talvez.", "Nossa!!! Sério??? Não... acredito!?! Fim.", "Sério?... Não sei."],
)
def test_no_punctuation_runs_reach_koko(raw):
    out = prep(raw)
    assert "..." not in out and "?!" not in out and "!?" not in out and "!!" not in out and "??" not in out
    for line in out.splitlines():
        assert any(ch.isalnum() for ch in line)  # no punctuation-only segment
    assert _words(out) == _words(raw)  # nothing is lost


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Fim.!", "Fim!"), ("Ok.? Sim.", "Ok?\nSim."), ("Isso!. Não.", "Isso!\nNão."),
     ('Ele disse "Pare!". Parei.', 'Ele disse "Pare"!\nParei.'), ("Etc…. Fim.", "Etc…\nFim."),
     ("(Ok!). Sim.", "(Ok)!\nSim."), ("Ele disse “fim.”. Ok.", "Ele disse “fim”.\nOk.")],
)
def test_mixed_sign_runs_become_one_sign(raw, expected):
    # Runs that mix "." with !?… — or are split by closing quotes/brackets —
    # left a punctuation-only piece that crashed koko.
    out = prep(raw)
    assert out == expected
    for line in out.splitlines():
        pieces = re.split(r"[.!?…]", line)
        assert pieces[-1] == ""  # the sentence ends at its sign
        assert all(any(ch.isalnum() for ch in p) for p in pieces[:-1])
    assert re.findall(r"\w+", out) == re.findall(r"\w+", raw)  # nothing is lost


def test_decimals_and_single_signs_are_kept():
    assert prep("Custa 3.5 mil. Versão 1.2!") == "Custa 3.5 mil.\nVersão 1.2!"


def test_punctuation_only_text_says_nothing():
    assert prep("...") == ""
    assert prep(" ?! … ") == ""


def test_long_sentences_are_split_below_the_model_limit():
    raw = " ".join(["palavra"] * 180)  # 1439 chars, one sentence: koko panicked
    lines = prep(raw).splitlines()
    assert len(lines) > 1
    assert all(len(line) <= kvs.KOKO_MAX_SEGMENT_CHARS + 1 for line in lines)
    assert all(line[-1] in ".!?…;:" for line in lines)
    assert _words("\n".join(lines)) == _words(raw)


def test_long_sentence_prefers_commas():
    raw = ", ".join(["uma parte da frase com algumas palavras"] * 10)
    lines = prep(raw, limit=100).splitlines()
    assert all(len(line) <= 101 for line in lines)
    assert lines[0].endswith(".")  # each piece is its own sentence


def test_word_longer_than_the_limit_is_cut():
    lines = prep("a" * 50, limit=20).splitlines()
    assert all(len(line) <= 21 for line in lines)


def test_ordinary_text_is_kept():
    assert prep("Olá, tudo bem? Sim.") == "Olá, tudo bem?\nSim."
