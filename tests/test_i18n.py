"""Regression tests for development-time translation loading."""

from pathlib import Path

import utils.i18n as i18n


def test_development_locale_directory_points_to_repository() -> None:
    """The source checkout must win over a system-installed catalog."""
    repository_root = Path(__file__).resolve().parent.parent
    assert i18n._po_dirs[0] == repository_root / "locale"
