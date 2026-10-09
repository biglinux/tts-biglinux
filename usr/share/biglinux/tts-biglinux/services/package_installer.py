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
- A package may be spelled two ways: BigLinux stable has
  ``piper-voices-pt-br``, testing ``piper-voices-pt-BR``. resolve() tries both
  (an installed spelling first: both ship the same files), and the AUR only
  as a last resort, through pamac (or paru/yay) as the person, never root.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from utils.i18n import _

logger = logging.getLogger(__name__)

PACMAN_LOCK = Path("/var/lib/pacman/db.lck")
PAMAC_CONF = Path("/etc/pamac.conf")
AUR_RPC = "https://aur.archlinux.org/rpc/v5/info"
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


@dataclass
class Resolution:
    """How a wanted package can be had."""
    wanted: str
    name: str = ""  # the spelling to install (or the one installed)
    source: str = ""  # "installed" | "repo" | "aur" | "" (nowhere)


def name_variants(name: str) -> list[str]:
    """The spellings to look for, lower case first ("piper-voices-pt-BR" →
    ["piper-voices-pt-br", "piper-voices-pt-BR"]): BigLinux stable and the
    AUR use lower case."""
    return list(dict.fromkeys([name.lower(), name]))


def aur_helper() -> list[str] | None:
    """argv prefix that builds and installs AUR packages, or None.

    pamac (BigLinux's own) when its AUR support is on; otherwise paru or
    yay, which ask for the password through pkexec (no terminal here).
    """
    if shutil.which("pamac") and _pamac_aur_enabled():
        return ["pamac", "build", "--no-confirm"]
    if shutil.which("paru"):
        return ["paru", "-S", "--aur", "--needed", "--noconfirm", "--skipreview", "--sudo", "pkexec"]
    if shutil.which("yay"):
        return ["yay", "-S", "--aur", "--needed", "--noconfirm",
                "--answerdiff", "None", "--answerclean", "None", "--sudo", "pkexec"]
    return None


def _pamac_aur_enabled() -> bool:
    try:
        lines = PAMAC_CONF.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(line.split("#", 1)[0].strip() == "EnableAUR" for line in lines)


def aur_names(names: list[str], timeout: float = 10.0) -> dict[str, str]:
    """Which of ``names`` exist in the AUR (case-insensitive): lower-case
    name → the AUR's spelling. Empty when the AUR cannot be reached."""
    names = [n for n in names if _SAFE_PKG.match(n)]
    if not names:
        return {}
    query = urllib.parse.urlencode([("arg[]", n) for n in names])
    try:
        with urllib.request.urlopen(f"{AUR_RPC}?{query}", timeout=timeout) as resp:
            data = json.load(resp)
    except (OSError, ValueError) as e:
        logger.debug("AUR query failed: %s", e)
        return {}
    return {r["Name"].lower(): r["Name"] for r in data.get("results", []) if r.get("Name")}


def resolve(packages: list[str], *, allow_aur: bool = True) -> list[Resolution]:
    """Find each package under either spelling: installed, then in the
    repositories, then (last resort) in the AUR if a helper can build it."""
    result: list[Resolution] = []
    for wanted in packages:
        variants = name_variants(wanted)
        found = next((Resolution(wanted, v, "installed") for v in variants if is_installed(v)), None)
        if found is None:
            found = next((Resolution(wanted, v, "repo") for v in variants if query(v).available), None)
        result.append(found or Resolution(wanted))
    missing = [r for r in result if not r.source]
    if missing and allow_aur and aur_helper():
        in_aur = aur_names([r.wanted for r in missing])
        for r in missing:
            if r.wanted.lower() in in_aur:
                r.name, r.source = in_aur[r.wanted.lower()], "aur"
    return result


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


def install_aur(packages: list[str], timeout: int = 3600) -> InstallResult:
    """Build and install AUR packages as the person (blocking: worker thread).

    Never as root: AUR helpers refuse it, and a PKGBUILD must not run as
    root. The helper asks for the password itself (polkit) to install.
    """
    # No "--" (pamac does not take it): a name can never start with "-".
    packages = [p for p in packages if _SAFE_PKG.match(p) and not p.startswith("-")]
    if not packages:
        return InstallResult(False, _("Nothing to install."))
    helper = aur_helper()
    if helper is None:
        return InstallResult(False, _("{packages} is not available in your repositories. Update the system (sudo pacman -Syu) and try again.").format(
            packages=", ".join(packages)), missing=packages)
    if PACMAN_LOCK.exists():
        return InstallResult(
            False,
            _("Another program is installing or updating packages. Wait for it to finish and try again."),
        )
    try:
        proc = subprocess.run(
            [*helper, *packages],
            capture_output=True, text=True, timeout=timeout, env=_C_ENV, stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return InstallResult(False, _("{packages} could not be built from the AUR. See the details.").format(
            packages=", ".join(packages)), helper[0])
    except subprocess.TimeoutExpired:
        return InstallResult(False, _("The installation took too long and was stopped."))
    if proc.returncode == 0:
        return InstallResult(True)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.lower().startswith(("error", "==> error"))]
    detail = "\n".join(errors or lines[-8:])
    if "unable to lock database" in output.lower():
        message = _("Another program is installing or updating packages. Wait for it to finish and try again.")
    else:
        message = _("{packages} could not be built from the AUR. See the details.").format(packages=", ".join(packages))
    logger.warning("AUR install failed (%d): %s", proc.returncode, detail)
    return InstallResult(False, message, detail)


__all__ = [
    "InstallResult", "PackageInfo", "Resolution", "aur_helper", "aur_names", "explain_failure",
    "install", "install_aur", "is_installed", "name_variants", "query", "resolve",
]
