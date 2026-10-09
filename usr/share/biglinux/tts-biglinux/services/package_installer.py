"""
Install engine/voice packages with pacman — checked first, explained after.

Used by the "install Piper / Kokoro" prompts. Rules:

- Check availability first (``pacman -Si``, no root): never start an install
  that cannot succeed, and say which package is missing instead.
- ``pkexec pacman -S --needed`` only. Never ``-Sy``: refreshing the package
  databases without upgrading the system is a partial upgrade, which can
  break an Arch/Manjaro system.
- pacman runs with ``LC_ALL=C`` so its output can be parsed; the person gets a
  translated explanation, and the raw ``error:`` lines as technical detail.
  (The old code showed the last stderr line, which was often a harmless
  warning such as "espeak-ng-1.52.0-1 is up to date -- skipping".)
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from utils.i18n import _

logger = logging.getLogger(__name__)

PACMAN_LOCK = Path("/var/lib/pacman/db.lck")
_SAFE_PKG = re.compile(r"^[A-Za-z0-9@._+-]+$")
_C_ENV = {**os.environ, "LC_ALL": "C", "LANG": "C"}


@dataclass
class PackageInfo:
    name: str
    available: bool
    repository: str = ""
    download_size: str = ""  # as pacman prints it, e.g. "88.20 MiB"


@dataclass
class InstallResult:
    ok: bool
    message: str = ""  # translated, says what to do
    detail: str = ""  # pacman's own error lines (technical)
    missing: list[str] = field(default_factory=list)


def query(name: str) -> PackageInfo:
    """Is ``name`` in the configured repositories? (no root, read-only)"""
    if not _SAFE_PKG.match(name):
        return PackageInfo(name, False)
    try:
        proc = subprocess.run(
            ["pacman", "-Si", "--", name],
            capture_output=True, text=True, timeout=20, env=_C_ENV,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("pacman -Si %s failed: %s", name, e)
        return PackageInfo(name, False)
    if proc.returncode != 0:
        return PackageInfo(name, False)
    fields = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields.setdefault(key.strip(), value.strip())
    return PackageInfo(
        name, True,
        repository=fields.get("Repository", ""),
        download_size=fields.get("Download Size", ""),
    )


def is_installed(name: str) -> bool:
    try:
        return subprocess.run(
            ["pacman", "-Q", "--", name], capture_output=True, timeout=10
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def explain_failure(returncode: int, output: str, packages: list[str]) -> InstallResult:
    """Turn pacman/pkexec output into a message the person can act on."""
    errors = [ln.strip() for ln in output.splitlines() if ln.strip().lower().startswith("error:")]
    detail = "\n".join(errors) or "\n".join(output.strip().splitlines()[-5:])
    text = output.lower()
    names = ", ".join(packages)
    if returncode in (126, 127) and not errors:
        message = _("The installation was cancelled: the administrator password was not confirmed.")
    elif "target not found" in text:
        missing = re.findall(r"target not found:\s*(\S+)", output)
        message = _("{packages} is not available in your repositories. Update the system (sudo pacman -Syu) and try again.").format(
            packages=", ".join(missing) or names
        )
    elif "unable to lock database" in text:
        message = _("Another program is installing or updating packages. Wait for it to finish and try again.")
    elif "failed retrieving file" in text or "could not resolve host" in text or "failed to synchronize" in text:
        message = _("The packages could not be downloaded. Check your internet connection and try again.")
    elif "conflicting files" in text or "are in conflict" in text:
        message = _("{packages} conflicts with installed files or packages. See the details.").format(packages=names)
    elif "not enough free disk space" in text:
        message = _("Not enough free disk space to install {packages}.").format(packages=names)
    else:
        message = _("{packages} could not be installed. See the details.").format(packages=names)
    return InstallResult(False, message, detail)


def install(packages: list[str], timeout: int = 1800) -> InstallResult:
    """``pkexec pacman -S --needed`` the packages (blocking: worker thread)."""
    packages = [p for p in packages if _SAFE_PKG.match(p)]
    if not packages:
        return InstallResult(False, _("Nothing to install."))
    if PACMAN_LOCK.exists():
        return InstallResult(
            False,
            _("Another program is installing or updating packages. Wait for it to finish and try again."),
        )
    try:
        proc = subprocess.run(
            ["pkexec", "env", "LC_ALL=C", "LANG=C",
             "pacman", "-S", "--noconfirm", "--needed", "--", *packages],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return InstallResult(False, _("pkexec is not installed (package polkit)."))
    except subprocess.TimeoutExpired:
        return InstallResult(False, _("The installation took too long and was stopped."))
    except PermissionError as e:
        # On timeout subprocess kills pkexec; a root child refuses (EPERM).
        return InstallResult(False, _("The installation took too long and was stopped."), str(e))
    if proc.returncode == 0:
        return InstallResult(True)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    result = explain_failure(proc.returncode, output, packages)
    logger.warning("Package install failed (%d): %s", proc.returncode, result.detail)
    return result


__all__ = ["InstallResult", "PackageInfo", "explain_failure", "install", "is_installed", "query"]
