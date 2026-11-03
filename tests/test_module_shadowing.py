"""A module installed by `pip install --user` must never replace the app's own.

hermes-agent drops a top-level utils.py into ~/.local/lib/pythonX.Y/site-packages.
While utils/ had no __init__.py it was a namespace package, which loses to any
plain module on sys.path, and the app died at start with
"No module named 'utils.i18n'; 'utils' is not a package".
"""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "usr" / "share" / "biglinux" / "tts-biglinux"
DECOYS = ("utils", "services", "ui", "config", "resources", "application", "window")


def _user_site(base: Path) -> Path:
    rel = sysconfig.get_path("purelib", f"{os.name}_user", {"userbase": str(base)})
    return Path(rel)


@pytest.fixture
def poisoned_user_site(tmp_path):
    """A user site-packages full of modules named like the app's packages."""
    site = _user_site(tmp_path)
    site.mkdir(parents=True)
    for name in DECOYS:
        (site / f"{name}.py").write_text("raise ImportError('decoy from user site')\n")
    env = dict(os.environ, PYTHONUSERBASE=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONNOUSERSITE", None)
    probe = subprocess.run(
        [sys.executable, "-c", "import site, sys; print(site.ENABLE_USER_SITE)"],
        env=env, capture_output=True, text=True,
    )
    if probe.stdout.strip() != "True":
        pytest.skip("user site-packages disabled in this environment")
    return env


@pytest.mark.parametrize("package", ["services", "ui", "utils", "resources"])
def test_every_app_package_is_a_regular_package(package):
    assert (APP_DIR / package / "__init__.py").is_file()


def test_app_starts_even_without_dash_s(poisoned_user_site):
    """The package layer alone keeps the app's modules first."""
    result = subprocess.run(
        [sys.executable, "-c", "import utils.i18n, services.settings_service, config"],
        cwd=APP_DIR, env=poisoned_user_site, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_main_runs_with_poisoned_user_site(poisoned_user_site):
    result = subprocess.run(
        [sys.executable, "-s", "main.py", "--version"],
        cwd=APP_DIR, env=poisoned_user_site, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "biglinux-tts" in result.stdout


@pytest.mark.parametrize("launcher", ["biglinux-tts", "biglinux-tts-speak"])
def test_launchers_ignore_user_site(launcher):
    text = (ROOT / "usr" / "bin" / launcher).read_text()
    runs = [line for line in text.splitlines() if "/usr/bin/python3" in line and "exec" in line]
    assert runs, "launcher no longer runs python3 directly"
    assert all("python3 -s " in line for line in runs)


def test_tray_helper_ignores_user_site():
    from services import tray_service

    source = Path(tray_service.__file__).read_text()
    assert '"/usr/bin/python3",\n            "-s",' in source
