"""Unit tests for the pure TTS parameter mapping functions.

Contract (mission requirements):
  - volume 0 == true MUTE for every backend
  - rate: left(-100)=slowest, right(+100)=fastest, monotonic
  - piper length_scale: slower speech => larger scale
"""
import importlib

ts = importlib.import_module("services.tts_service")


def test_volume_zero_is_mute():
    assert ts.volume_factor(0) == 0.0
    assert ts.espeak_volume(0) == 0


def test_volume_monotonic_and_bounded():
    assert ts.volume_factor(50) == 1.0
    assert ts.volume_factor(100) == 2.0
    assert 0.0 < ts.volume_factor(1) <= ts.volume_factor(100)
    assert 0 <= ts.espeak_volume(100) <= 200


def test_rate_left_slower_right_faster():
    assert ts.espeak_wpm(-100) < ts.espeak_wpm(0) < ts.espeak_wpm(100)
    # Piper: faster speech => smaller length_scale
    assert ts.piper_length_scale(100) < ts.piper_length_scale(0) < ts.piper_length_scale(-100)


def test_ranges_clamped():
    assert 80 <= ts.espeak_wpm(-9999) <= 450
    assert 80 <= ts.espeak_wpm(9999) <= 450
    assert 0 <= ts.espeak_pitch(-9999) <= 99
    assert 0 <= ts.espeak_pitch(9999) <= 99
