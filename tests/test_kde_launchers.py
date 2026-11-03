"""Old speak-shortcut launchers in ~/.local/share/kglobalaccel/ must go.

kglobalacceld loads every .desktop file there at login; old copies of ours
(bigtts.desktop, tts-speak.desktop) each claimed Alt+V with their default
X-KDE-Shortcuts, so the shortcut failed until the app had started once.
"""
from pathlib import Path

import pytest

from services.desktop_integration_service import DesktopIntegrationService as DIS

OLD_LAUNCHER = """[Desktop Entry]
Type=Application
Exec=biglinux-tts-speak
Name=Speech or stop selected text
X-KDE-Shortcuts=Alt+V
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".local" / "share" / "kglobalaccel").mkdir(parents=True)
    return tmp_path


def _kga(home: Path) -> Path:
    return home / ".local" / "share" / "kglobalaccel"


def test_old_launchers_of_ours_are_removed(home):
    for name in ("bigtts.desktop", "tts-speak.desktop"):
        (_kga(home) / name).write_text(OLD_LAUNCHER)
    (_kga(home) / "tts-speak.desktop").write_text(
        OLD_LAUNCHER.replace("Exec=biglinux-tts-speak", "Exec=/home/x/.local/bin/biglinux-tts-speak")
    )

    removed = DIS.remove_stale_kde_launchers()

    assert sorted(p.name for p in removed) == ["bigtts.desktop", "tts-speak.desktop"]
    assert list(_kga(home).iterdir()) == []


def test_foreign_launcher_with_same_name_is_kept(home):
    other = _kga(home) / "tts-speak.desktop"
    other.write_text("[Desktop Entry]\nType=Application\nExec=someone-elses-tts\n")

    assert DIS.remove_stale_kde_launchers() == []
    assert other.exists()


def test_current_launcher_is_never_removed(home):
    ours = _kga(home) / "biglinux-tts-speak.desktop"
    ours.write_text(OLD_LAUNCHER)

    DIS.remove_stale_kde_launchers()

    assert ours.exists()


def test_missing_dir_is_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert DIS.remove_stale_kde_launchers() == []


def test_kglobalaccel_copy_follows_the_configured_key(home):
    copy = _kga(home) / "biglinux-tts-speak.desktop"
    copy.write_text(OLD_LAUNCHER)

    written = DIS.ensure_desktop_file("Ctrl+Alt+S")

    assert "X-KDE-Shortcuts=Ctrl+Alt+S\n" in copy.read_text()
    assert copy.read_text() == written.read_text()


def test_no_kglobalaccel_copy_is_created(home):
    DIS.ensure_desktop_file("Alt+V")
    assert not (_kga(home) / "biglinux-tts-speak.desktop").exists()
