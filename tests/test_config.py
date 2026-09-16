"""Config robustness — one bad key must not wipe all preferences."""
import importlib
cfg = importlib.import_module("config")


def test_bad_field_falls_back_not_wipes_all():
    data = {
        "speech": {"rate": "not-a-number", "volume": 42, "backend": "piper"},
        "text": {"normalize_numbers": "true", "max_chars": "oops"},
        "show_welcome": False,
    }
    s = cfg._deserialize_settings(data)
    # bad rate -> default, but valid siblings preserved
    assert s.speech.rate == cfg.RATE_DEFAULT
    assert s.speech.volume == 42
    assert s.speech.backend == "piper"
    assert s.text.normalize_numbers is True           # "true" coerced
    assert s.text.max_chars == cfg.MAX_CHARS_DEFAULT   # bad -> default
    assert s.show_welcome is False


def test_config_version_present():
    s = cfg._deserialize_settings({})
    assert s.config_version == cfg.CONFIG_VERSION


def test_nondict_subsection_does_not_crash():
    s = cfg._deserialize_settings({"speech": "garbage", "kokoro": 5})
    assert isinstance(s.speech.rate, int)
