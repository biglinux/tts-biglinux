"""Preview must never default to English for non-English/unknown voices."""
import os
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "usr" / "share" / "biglinux" / "tts-biglinux"
sys.path.insert(0, str(APP))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
try:
    from ui.voice_manager_dialog import VoiceManagerDialog
except Exception as e:  # pragma: no cover
    pytest.skip(f"GTK unavailable headless: {e}", allow_module_level=True)


def test_espeak_voice_for_pt(monkeypatch):
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    assert VoiceManagerDialog._espeak_voice_for_lang("pt_BR") == "pt-br"
    assert VoiceManagerDialog._espeak_voice_for_lang("pt") == "pt"


def test_espeak_voice_unknown_uses_system(monkeypatch):
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    # unknown/multi must NOT become English on a pt system
    assert VoiceManagerDialog._espeak_voice_for_lang("multi") == "pt-br"
    assert VoiceManagerDialog._espeak_voice_for_lang("") == "pt-br"


def test_espeak_voice_explicit_es(monkeypatch):
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    assert VoiceManagerDialog._espeak_voice_for_lang("es") == "es"
