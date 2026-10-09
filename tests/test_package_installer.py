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


# ── Two spellings, then the AUR ──────────────────────────────────────
# BigLinux stable has piper-voices-pt-br, testing piper-voices-pt-BR, the AUR
# piper-voices-pt-br. Both repository packages ship the same voice files.

def _world(monkeypatch, repo=(), installed=(), aur=(), helper=True):
    asked_aur = []
    monkeypatch.setattr(pi, "is_installed", lambda n: n in installed)
    monkeypatch.setattr(pi, "query", lambda n: pi.PackageInfo(n, n in repo))

    def fake_aur(names, timeout=10.0):
        asked_aur.append(list(names))
        known = {a.lower(): a for a in aur}
        return {n.lower(): known[n.lower()] for n in names if n.lower() in known}

    monkeypatch.setattr(pi, "aur_names", fake_aur)
    monkeypatch.setattr(pi, "aur_helper", lambda: ["pamac", "build", "--no-confirm"] if helper else None)
    return asked_aur


def _one(wanted="piper-voices-pt-BR"):
    [r] = pi.resolve([wanted])
    return r.source, r.name


def test_both_spellings_are_tried():
    assert pi.name_variants("piper-voices-pt-BR") == ["piper-voices-pt-br", "piper-voices-pt-BR"]
    assert pi.name_variants("piper-tts-bin") == ["piper-tts-bin"]


def test_stable_spelling_is_found(monkeypatch):
    _world(monkeypatch, repo={"piper-voices-pt-br"})
    assert _one() == ("repo", "piper-voices-pt-br")


def test_testing_spelling_is_found(monkeypatch):
    _world(monkeypatch, repo={"piper-voices-pt-BR"})
    assert _one() == ("repo", "piper-voices-pt-BR")


def test_lower_case_wins_when_both_exist(monkeypatch):
    _world(monkeypatch, repo={"piper-voices-pt-BR", "piper-voices-pt-br"})
    assert _one() == ("repo", "piper-voices-pt-br")


def test_an_installed_spelling_is_never_installed_again(monkeypatch):
    # Installing the other spelling would fail on the shared voice files.
    _world(monkeypatch, repo={"piper-voices-pt-br", "piper-voices-pt-BR"}, installed={"piper-voices-pt-BR"})
    assert _one() == ("installed", "piper-voices-pt-BR")


def test_aur_only_as_a_last_resort(monkeypatch):
    asked = _world(monkeypatch, repo={"piper-voices-pt-br"}, aur={"piper-voices-pt-br"})
    assert _one() == ("repo", "piper-voices-pt-br")
    assert asked == []  # found in the repositories: the AUR is not even asked
    _world(monkeypatch, aur={"piper-voices-pt-br"})
    assert _one() == ("aur", "piper-voices-pt-br")


def test_without_an_aur_helper_the_package_is_missing(monkeypatch):
    asked = _world(monkeypatch, aur={"piper-voices-pt-br"}, helper=False)
    assert _one() == ("", "")
    assert asked == []  # no network query for nothing


def test_nowhere_is_missing(monkeypatch):
    _world(monkeypatch)
    assert _one() == ("", "")


def test_aur_names_are_case_insensitive(monkeypatch):
    import io
    import json

    seen = {}

    def fake_urlopen(url, timeout):
        seen["url"] = url
        return io.BytesIO(json.dumps({"results": [{"Name": "piper-voices-pt-br"}]}).encode())

    monkeypatch.setattr(pi.urllib.request, "urlopen", fake_urlopen)
    assert pi.aur_names(["piper-voices-pt-BR"]) == {"piper-voices-pt-br": "piper-voices-pt-br"}
    assert seen["url"].startswith("https://aur.archlinux.org/rpc/v5/info?")

    def offline(url, timeout):
        raise OSError("no network")

    monkeypatch.setattr(pi.urllib.request, "urlopen", offline)
    assert pi.aur_names(["piper-voices-pt-BR"]) == {}


def test_aur_helper_choice(monkeypatch, tmp_path):
    conf = tmp_path / "pamac.conf"
    monkeypatch.setattr(pi, "PAMAC_CONF", conf)
    tools = set()
    monkeypatch.setattr(pi.shutil, "which", lambda t: f"/usr/bin/{t}" if t in tools else None)
    assert pi.aur_helper() is None
    tools.update({"pamac", "yay"})
    conf.write_text("#EnableAUR\n")
    assert pi.aur_helper()[0] == "yay"  # pamac without its AUR support
    conf.write_text("EnableAUR\n")
    assert pi.aur_helper()[:2] == ["pamac", "build"]
    tools.discard("pamac")
    tools.add("paru")
    helper = pi.aur_helper()
    assert helper[0] == "paru" and helper[-2:] == ["--sudo", "pkexec"]  # no terminal for sudo


def test_install_aur_runs_as_the_person(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(pi.subprocess, "run", fake_run)
    monkeypatch.setattr(pi, "PACMAN_LOCK", pi.Path("/nonexistent/db.lck"))
    monkeypatch.setattr(pi, "aur_helper", lambda: ["pamac", "build", "--no-confirm"])
    assert pi.install_aur(["piper-voices-pt-br", "-rf"]).ok
    assert seen["cmd"] == ["pamac", "build", "--no-confirm", "piper-voices-pt-br"]  # not pkexec, no option-like name


def test_install_aur_failure_is_explained(monkeypatch):
    out = "==> ERROR: Failure while downloading https://huggingface.co/x.onnx\n    Aborting...\n"
    monkeypatch.setattr(pi.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, out, ""))
    monkeypatch.setattr(pi, "PACMAN_LOCK", pi.Path("/nonexistent/db.lck"))
    monkeypatch.setattr(pi, "aur_helper", lambda: ["pamac", "build", "--no-confirm"])
    r = pi.install_aur(["piper-voices-pt-br"])
    assert not r.ok and "piper-voices-pt-br" in r.message
    assert "Failure while downloading" in r.detail
