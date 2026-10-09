"""Fake engine programs for the tests: real processes, no sound.

- ``koko``: like ``koko pipe -o FILE`` — reads stdin one line at a time,
  appends the line's audio to FILE (float32 24 kHz WAV, header sizes never
  filled in), says "Streaming audio" on stderr and "plays" it:
  ``$KOKO_PLAY_SECONDS`` per line (default 0.1; 0 = no wait, 0.1 s of audio). ``KOKO_MODE``: ok | fail | abort | slow;
  ``KOKO_STDIN_COPY`` saves what it read.
- ``RHVoice-test``: reads stdin, writes a 16-bit WAV to stdout.
- ``aplay``: reads a WAV from stdin or from its file argument, appends what it
  played to ``$PLAYLOG`` (missing files are logged as "missing …"), and takes
  ``$APLAY_SECONDS`` like real playback.

/tmp may be mounted noexec, so the programs live in tests/tmp/ (git-ignored).
"""

from __future__ import annotations

import atexit
import os
import shutil
import stat
import struct
import sys
import tempfile
import wave
from pathlib import Path

PY = sys.executable

KOKO = f"""#!{PY}
import os, struct, sys, time
args = sys.argv[1:]
mode = os.environ.get("KOKO_MODE", "ok")
play = float(os.environ.get("KOKO_PLAY_SECONDS", "0.1"))
if mode == "slow":
    time.sleep(1)
if mode == "fail":
    sys.stderr.write("Error: Os {{ code: 13, kind: PermissionDenied, message: \\"Permission denied\\" }}\\n")
    sys.exit(1)
if mode == "abort":
    sys.stderr.write("Application panic: panicked at kokorox/src/tts/koko.rs:1160:40:\\n")
    sys.stderr.flush()
    os.kill(os.getpid(), 6)
assert args[-3:-1] == ["pipe", "-o"], args
copy = os.environ.get("KOKO_STDIN_COPY")
copy = open(copy, "wb") if copy else None
out = open(args[-1], "wb")
out.write(b"RIFF" + struct.pack("<I", 0) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 3, 1, 24000, 96000, 4, 32))
out.write(b"data" + struct.pack("<I", 0))
out.flush()
for raw in iter(sys.stdin.buffer.readline, b""):
    if copy:
        copy.write(raw)
        copy.flush()
    if raw.strip():
        out.write(struct.pack("<f", 0.1) * int(24000 * (play or 0.1)))  # written before it plays
        out.flush()
        sys.stderr.write("Streaming audio for this segment...\\n")
        sys.stderr.flush()
        time.sleep(play)  # koko pipe plays each line itself
"""

RHVOICE = f"""#!{PY}
import io, sys, wave
sys.stdin.buffer.read()
buf = io.BytesIO()
w = wave.open(buf, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
w.writeframes(b"\\x01\\x00" * 4800); w.close()
sys.stdout.buffer.write(buf.getvalue())
"""

APLAY = f"""#!{PY}
import os, sys, time
args = [a for a in sys.argv[1:] if not a.startswith("-")]
log = os.environ.get("PLAYLOG")
if args:
    path = args[0]
    if not os.path.exists(path):
        if log:
            open(log, "a").write("missing " + path + "\\n")
        sys.exit(3)
    data = open(path, "rb").read()
else:
    data = sys.stdin.buffer.read()
if log:
    text = data[44:120].decode("ascii", "ignore").strip("\\x00 ") if data.startswith(b"RIFF") else ""
    open(log, "a").write((text or str(len(data))) + "\\n")
time.sleep(float(os.environ.get("APLAY_SECONDS", "0.05")))
"""


def make_bin_dir() -> Path:
    base = Path(__file__).resolve().parent / "tmp"
    base.mkdir(exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=base))
    atexit.register(shutil.rmtree, d, True)
    return d


def install(bindir: Path, name: str, script: str) -> Path:
    exe = bindir / name
    exe.write_text(script)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def install_all(bindir: Path) -> None:
    install(bindir, "koko", KOKO)
    install(bindir, "RHVoice-test", RHVOICE)
    install(bindir, "aplay", APLAY)


def text_wav(path: Path, text: str) -> str:
    """A valid 0.25 s WAV whose first samples spell ``text`` (for the play log)."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        # The text first (the fake aplay logs it), then silence: 0.25 s.
        w.writeframes(text.encode("ascii").ljust(64, b" ").ljust(22050 // 2, b"\x00"))
    return str(path)


def use_fakes(monkeypatch, tmp_path: Path) -> Path:
    """Put the fake programs first on PATH and set up Kokoro's files."""
    bindir = make_bin_dir()
    install_all(bindir)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("PLAYLOG", str(tmp_path / "played.log"))
    model = tmp_path / "model.onnx"
    model.write_bytes(b"x")
    voices = tmp_path / "voices.bin"
    voices.write_bytes(b"x")
    monkeypatch.setenv("KOKO_MODEL_PATH", str(model))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    import importlib

    kvs = importlib.import_module("services.kokoro_voice_service")
    monkeypatch.setattr(kvs, "_koko_workdir_cache", None)
    monkeypatch.setattr(kvs, "get_active_voices_bin", lambda: voices)
    monkeypatch.setattr(kvs, "get_installed_voice_ids", lambda: {"pm_alex", "pf_dora"})
    return bindir


def played(tmp_path: Path) -> list[str]:
    log = tmp_path / "played.log"
    return [ln for ln in log.read_text().splitlines() if ln] if log.exists() else []


def wav_seconds(path: Path) -> float:
    head = path.read_bytes()[:4096]
    data = head.find(b"data")
    rate = struct.unpack("<I", head[28:32])[0]
    return (path.stat().st_size - data - 8) / rate
