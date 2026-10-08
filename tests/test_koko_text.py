"""Text sent to `koko pipe` must be readable to the end.

Real failures reproduced with koko (kokorox v0.2.2):
- "Espere... isso" / "Sério?! Não" → exit 1 (SendError): koko cuts at each
  sign and chokes on the punctuation-only piece in between;
- a sentence longer than the model's 510 phoneme tokens → panic
  ("index out of bounds: the len is 511 but the index is 550").
"""
import importlib

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
