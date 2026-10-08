"""Native Rust engine regression tests (skipped if engine unavailable).

Locks in:
  - Piper native synthesis works and opens NO audio device (English-intro guard)
  - espeak used as phonemizer / synth never opens the audio device
  - espeak synth honors volume=0 as TRUE mute (silent PCM)
"""
import glob
import os
import struct
import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent / "tts-engine" / "target" / "release"
sys.path.insert(0, str(ENGINE_DIR))

tts_engine = pytest.importorskip("tts_engine")

MODEL = "/usr/share/piper-voices/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx"
pytestmark = pytest.mark.skipif(
    not os.path.isfile(MODEL), reason="pt_BR Piper model not installed"
)


def _audio_fds():
    out = []
    for f in glob.glob("/proc/self/fd/*"):
        try:
            link = os.readlink(f)
        except OSError:
            continue
        if any(k in link.lower() for k in ("snd", "pipewire", "pulse", "/dev/dsp")):
            out.append(link)
    return out


def _max_amplitude(wav: bytes) -> int:
    data = wav[44:]
    return max(
        (abs(struct.unpack("<h", data[i:i + 2])[0]) for i in range(0, len(data) - 1, 2)),
        default=0,
    )


def test_piper_native_synth_opens_no_audio_device():
    before = set(_audio_fds())
    wav = tts_engine.synthesize_piper("Bom dia, tudo bem?", MODEL, 1.0, 0.667, 0.8, 1.0)
    assert len(wav) > 1000
    assert set(_audio_fds()) == before, "synthesis must not open the audio device"


def test_piper_volume_zero_is_silent():
    wav = tts_engine.synthesize_piper("Bom dia", MODEL, 1.0, 0.667, 0.8, 0.0)
    assert _max_amplitude(wav) == 0, "volume 0 must be true mute"


def test_prewarm_is_audio_free():
    before = set(_audio_fds())
    tts_engine.load_piper(MODEL)  # must never open the audio device
    assert set(_audio_fds()) == before, "prewarm must not open the audio device"
    # After prewarm, a synth still succeeds (warm path)
    wav = tts_engine.synthesize_piper("Olá", MODEL, 1.0, 0.667, 0.8, 1.0)
    assert len(wav) > 1000


def test_espeak_phonemizer_is_audio_free_and_muteable():
    before = set(_audio_fds())
    wav = tts_engine.synthesize_espeak("Bom dia, tudo bem?", "pt-br", 175, 50, 100)
    assert _max_amplitude(wav) > 0, "espeak must produce audio at volume 100"
    assert set(_audio_fds()) == before, "espeak synth must not open the audio device"
    wav0 = tts_engine.synthesize_espeak("Bom dia", "pt-br", 175, 50, 0)
    assert _max_amplitude(wav0) == 0, "espeak volume 0 must be true mute"
