"""Text chunking tests."""
import importlib
tp = importlib.import_module("services.text_processor")


def test_short_text_single_chunk():
    assert tp.chunk_text("Bom dia.", 2000) == ["Bom dia."]


def test_empty():
    assert tp.chunk_text("", 100) == []
    assert tp.chunk_text("   ", 100) == []


def test_respects_max_chars():
    text = ". ".join(f"Frase número {i}" for i in range(500))
    chunks = tp.chunk_text(text, 200)
    assert all(len(c) <= 200 for c in chunks), [len(c) for c in chunks if len(c) > 200][:3]
    # Reassembling keeps all words
    joined = " ".join(chunks)
    assert "Frase número 0" in joined and "Frase número 499" in joined


def test_prefers_sentence_boundaries():
    text = "Primeira frase aqui. Segunda frase aqui. Terceira frase aqui."
    chunks = tp.chunk_text(text, 40)
    # No chunk should split in the middle of a word
    for c in chunks:
        assert not c.startswith(" ") and not c.endswith(" ")


def test_pathological_no_spaces():
    text = "a" * 5000
    chunks = tp.chunk_text(text, 1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert sum(len(c) for c in chunks) == 5000


def test_huge_text_bounded():
    text = ("A inteligência artificial transforma tudo. " * 3000)
    chunks = tp.chunk_text(text, 2000)
    assert all(len(c) <= 2000 for c in chunks)
    assert len(chunks) > 1
