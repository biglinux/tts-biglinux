#!/usr/bin/env python3
"""Structured TTS benchmark for the native Rust engine (Piper + espeak).

Measures synthesis TTFA (time to first audio *bytes*), total synth time, and
real-time factor (RTF = synth_time / audio_duration). Outputs JSON.

Usage:
    ORT_LIB_LOCATION=/usr/lib python3 scripts/benchmark_tts.py [--model PATH]
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent / "tts-engine" / "target" / "release"
sys.path.insert(0, str(ENGINE_DIR))

TEXTS = {
    10: "Bom dia.",
    50: "O rato roeu a roupa do rei de Roma ontem à noite.",
    200: ("A tecnologia de síntese de fala evoluiu muito nos últimos anos, "
          "permitindo vozes cada vez mais naturais e expressivas em português. "),
    1000: ("A inteligência artificial transforma a computação. " * 18),
}


def wav_duration_s(wav: bytes) -> float:
    if len(wav) < 44:
        return 0.0
    sample_rate = struct.unpack("<I", wav[24:28])[0]
    n = (len(wav) - 44) // 2
    return n / sample_rate if sample_rate else 0.0


def bench_piper(engine, model: str) -> list[dict]:
    rows = []
    # cold (first call loads the ONNX session)
    for i, (size, text) in enumerate(sorted(TEXTS.items())):
        t0 = time.perf_counter()
        wav = engine.synthesize_piper(text, model, 1.0, 0.667, 0.8, 1.0)
        dt = time.perf_counter() - t0
        dur = wav_duration_s(wav)
        rows.append({
            "backend": "piper-native", "chars": size,
            "cold": i == 0, "synth_s": round(dt, 4),
            "audio_s": round(dur, 3), "rtf": round(dt / dur, 4) if dur else None,
            "wav_bytes": len(wav),
        })
    return rows


def bench_espeak(engine) -> list[dict]:
    rows = []
    for i, (size, text) in enumerate(sorted(TEXTS.items())):
        t0 = time.perf_counter()
        wav = engine.synthesize_espeak(text, "pt-br", 175, 50, 100)
        dt = time.perf_counter() - t0
        dur = wav_duration_s(wav)
        rows.append({
            "backend": "espeak-native", "chars": size,
            "cold": i == 0, "synth_s": round(dt, 4),
            "audio_s": round(dur, 3), "rtf": round(dt / dur, 4) if dur else None,
            "wav_bytes": len(wav),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model",
                    default="/usr/share/piper-voices/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx")
    ap.add_argument("--json", action="store_true", help="emit raw JSON only")
    args = ap.parse_args()

    try:
        import tts_engine
    except ImportError as e:
        print(json.dumps({"error": f"engine unavailable: {e}"}))
        return 1

    results = {"engine_version": tts_engine.version(), "rows": []}
    results["rows"] += bench_espeak(tts_engine)
    if Path(args.model).is_file():
        results["rows"] += bench_piper(tts_engine, args.model)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print(f"engine v{results['engine_version']}")
        print(f"{'backend':16} {'chars':>6} {'cold':>5} {'synth_s':>8} {'audio_s':>8} {'rtf':>7}")
        for r in results["rows"]:
            print(f"{r['backend']:16} {r['chars']:>6} {str(r['cold']):>5} "
                  f"{r['synth_s']:>8} {r['audio_s']:>8} {str(r['rtf']):>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
