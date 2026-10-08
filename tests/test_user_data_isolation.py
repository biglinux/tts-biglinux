"""The test suite itself must never write the user's real history."""
import importlib
from pathlib import Path

hs = importlib.import_module("services.history_service")


def test_history_folder_is_redirected_for_tests():
    real = Path.home() / "Music"
    assert hs.get_history_dir().parent != real
    assert "user" in str(hs.get_history_dir())
