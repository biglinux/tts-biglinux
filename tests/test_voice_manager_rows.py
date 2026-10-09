"""Voice Manager rows: one per package, whatever the repository's spelling."""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
try:
    from ui.voice_manager_dialog import _one_per_spelling
except Exception as e:  # pragma: no cover
    pytest.skip(f"GTK unavailable headless: {e}", allow_module_level=True)


def test_voice_manager_shows_one_row_per_spelling():
    def row(name, installed="no"):
        return {"pkg": name, "installed": installed}

    rows = [row("piper-voices-pt-BR"), row("piper-voices-pt-PT"), row("piper-voices-pt-br")]
    assert [r["pkg"] for r in _one_per_spelling(rows)] == ["piper-voices-pt-PT", "piper-voices-pt-br"]
    rows[0]["installed"] = "yes"  # the installed spelling is the one shown
    assert [r["pkg"] for r in _one_per_spelling(rows)] == ["piper-voices-pt-BR", "piper-voices-pt-PT"]
