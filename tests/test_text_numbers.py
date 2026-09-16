"""pt-BR number/currency/percent normalization tests."""
import importlib
tp = importlib.import_module("services.text_processor")


def test_num_to_words_pt_examples():
    cases = {
        0: "zero", 1: "um", 2: "dois", 10: "dez", 15: "quinze", 20: "vinte",
        21: "vinte e um", 100: "cem", 101: "cento e um", 200: "duzentos",
        250: "duzentos e cinquenta", 999: "novecentos e noventa e nove",
        1000: "mil", 1001: "mil e um", 1250: "mil duzentos e cinquenta",
        2500: "dois mil e quinhentos", 2026: "dois mil e vinte e seis",
        1000000: "um milhão", 1000100: "um milhão e cem",
        1234567: "um milhão duzentos e trinta e quatro mil quinhentos e sessenta e sete",
    }
    for n, expected in cases.items():
        assert tp.num_to_words_pt(n) == expected, f"{n} -> {tp.num_to_words_pt(n)!r} != {expected!r}"


def test_currency_flagship():
    out = tp.process_text("R$ 1.250,90", language="pt")
    assert out == "mil duzentos e cinquenta reais e noventa centavos", out


def test_currency_singular():
    assert tp.process_text("R$ 1,00", language="pt") == "um real"
    assert tp.process_text("R$ 1,01", language="pt") == "um real e um centavo"


def test_percent():
    assert tp.process_text("50%", language="pt") == "cinquenta por cento"
    assert tp.process_text("3,5%", language="pt") == "três vírgula cinco por cento"


def test_grouped_number():
    assert tp.process_text("Havia 1.250 pessoas.", language="pt") == "Havia mil duzentos e cinquenta pessoas."


def test_disabled_toggle_keeps_raw():
    out = tp.process_text("R$ 1.250,90", language="pt", normalize_numbers=False,
                          process_special_chars=False)
    assert "1.250,90" in out, out


def test_non_pt_untouched():
    out = tp.process_text("R$ 1.250,90", language="en", process_special_chars=False)
    assert "1.250,90" in out or "1250" in out.replace(".", "")


def test_dates_pt():
    assert tp.process_text("15/09/2026", language="pt") == "quinze de setembro de dois mil e vinte e seis"
    assert tp.process_text("01/01/2000", language="pt").startswith("primeiro de janeiro")


def test_times_pt():
    assert tp.process_text("14:30", language="pt") == "catorze horas e trinta minutos"
    assert tp.process_text("9h", language="pt") == "nove horas"
    assert tp.process_text("1h", language="pt") == "uma hora" or tp.process_text("1h", language="pt") == "um hora"


def test_invalid_date_untouched():
    # 45/13 is not a valid date → left as-is (special chars will handle the slash)
    out = tp.process_text("45/13/2026", language="pt", process_special_chars=False, normalize_numbers=True)
    assert "45" in out
