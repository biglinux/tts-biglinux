"""Engine package installs: checked first, explained after, never -Sy."""
import importlib
import subprocess

pi = importlib.import_module("services.package_installer")

# What the old Kokoro installer got back: a harmless warning as the LAST line,
# the real error above it.
PACMAN_TARGET_NOT_FOUND = """warning: espeak-ng-1.52.0-1 is up to date -- skipping
error: target not found: python-kokoro
"""


def test_the_real_error_is_reported_not_the_last_warning():
    r = pi.explain_failure(1, PACMAN_TARGET_NOT_FOUND, ["python-kokoro", "espeak-ng"])
    assert not r.ok
    assert "python-kokoro" in r.message
    assert "error: target not found: python-kokoro" in r.detail
    assert "up to date" not in r.detail


def test_cancelled_password_is_explained():
    r = pi.explain_failure(126, "", ["biglinux-kokoro-tts"])
    assert r.message and not r.detail.startswith("error")


def test_network_and_lock_failures_are_explained():
    net = pi.explain_failure(1, "error: failed retrieving file 'x.pkg.tar.zst' from mirror", ["x"])
    lock = pi.explain_failure(1, "error: failed to init transaction (unable to lock database)", ["x"])
    assert net.message != lock.message
    assert "failed retrieving file" in net.detail


def test_query_reads_repository_and_size(monkeypatch):
    out = "Repository      : biglinux-stable\nName            : biglinux-kokoro-tts\nDownload Size   : 88.20 MiB\n"

    def fake_run(cmd, **kw):
        assert cmd[:2] == ["pacman", "-Si"] and kw["env"]["LC_ALL"] == "C"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(pi.subprocess, "run", fake_run)
    info = pi.query("biglinux-kokoro-tts")
    assert info.available and info.repository == "biglinux-stable" and info.download_size == "88.20 MiB"


def test_query_missing_package(monkeypatch):
    monkeypatch.setattr(pi.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "error: package 'x' was not found"))
    assert not pi.query("x").available


def test_install_never_refreshes_databases(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(pi.subprocess, "run", fake_run)
    monkeypatch.setattr(pi, "PACMAN_LOCK", pi.Path("/nonexistent/db.lck"))
    assert pi.install(["biglinux-kokoro-tts"]).ok
    cmd = seen["cmd"]
    assert cmd[0] == "pkexec" and "-S" in cmd and "--needed" in cmd
    assert not any(a.startswith("-Sy") for a in cmd)  # no partial upgrades
    assert cmd[-1] == "biglinux-kokoro-tts" and cmd[-2] == "--"


def test_unsafe_package_names_are_refused(monkeypatch):
    monkeypatch.setattr(pi.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert not pi.query("x; rm -rf /").available
    assert not pi.install(["--overwrite=*"]).ok
