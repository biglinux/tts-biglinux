"""Release metadata — one version and one author list everywhere."""
import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
cfg = importlib.import_module("config")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_version_is_the_same_everywhere():
    version = cfg.APP_VERSION
    cargo = re.search(r'^version = "([^"]+)"', _read("tts-engine/Cargo.toml"), re.M)
    lock = re.search(r'name = "tts-engine"\nversion = "([^"]+)"', _read("tts-engine/Cargo.lock"))
    pkgver = re.search(r"^pkgver=(\S+)$", _read("pkgbuild/PKGBUILD"), re.M)
    assert cargo and cargo.group(1) == version
    assert lock and lock.group(1) == version
    assert pkgver and pkgver.group(1) == version
    assert f"version-{version}-" in _read("README.md")


def test_pkgbuild_keeps_the_epoch():
    # Older packages were 26.x date versions: without epoch=1, pacman would
    # treat 4.x as a downgrade and never install it.
    assert re.search(r"^epoch=1$", _read("pkgbuild/PKGBUILD"), re.M)


def test_authors():
    names = [d.split(" <")[0] for d in cfg.APP_DEVELOPERS]
    assert names == ["Rafael Ruscher", "Bruno Gonçalves", "Tales A. Mendonça"]
