"""Voice manager pacman size parsing."""
import importlib
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
try:
    vm = importlib.import_module("ui.voice_manager_dialog")
except Exception as e:  # pragma: no cover
    pytest.skip(f"GTK unavailable: {e}", allow_module_level=True)


def test_humanize_size():
    assert vm._humanize_size("111.19 MiB") == "111.19 MB"
    assert vm._humanize_size("512.0 KiB") == "512.0 KB"
    assert vm._humanize_size("1.2 GiB") == "1.2 GB"


def test_annotate_sizes_kokoro_fixed():
    data = {"Kokoro": [
        {"pkg": "k1", "installed": "no"},
        {"pkg": "k2", "installed": "yes"},
    ]}
    vm._annotate_sizes(data)
    assert data["Kokoro"][0]["size"] == "≈ 0.5 MB"  # available gets an estimate
    assert "size" not in data["Kokoro"][1]           # installed base: no estimate
