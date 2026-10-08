"""Diagnostics collector — structure + formatting."""
import importlib
diag = importlib.import_module("services.diagnostics")


def test_collect_structure():
    d = diag.collect_diagnostics()
    for key in ("app_version", "python_version", "native_engine",
                "onnxruntime", "backends", "piper_voices_installed"):
        assert key in d
    assert isinstance(d["backends"], dict)
    assert isinstance(d["piper_voices_installed"], int)


def test_active_backend_from_settings():
    class Speech:
        backend = "piper"
        voice_id = "piper:/x.onnx"
    class Settings:
        speech = Speech()
    d = diag.collect_diagnostics(Settings())
    assert d["active_backend"] == "piper"
    assert d["active_voice"] == "piper:/x.onnx"


def test_format_is_text():
    d = diag.collect_diagnostics()
    txt = diag.format_diagnostics(d)
    assert "BigLinux TTS — Diagnostic" in txt
    assert "Backends detected:" in txt
    assert "ONNX Runtime:" in txt
