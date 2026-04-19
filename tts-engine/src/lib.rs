//! `BigLinux` TTS Engine — native Rust TTS backends with `PipeWire` audio.
//!
//! Provides Python bindings (`PyO3`) for:
//! - espeak-ng: direct FFI, no subprocess
//! - Piper: ONNX inference via ort + espeak-ng phonemization
//! - Audio: playback via rodio (PipeWire/ALSA auto-detected)

mod audio;
mod backends;
mod error;

use pyo3::prelude::*;

/// Python module: `import tts_engine`
#[pymodule]
fn tts_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Wire Rust log → Python logging
    pyo3_log::init();

    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(speak_espeak, m)?)?;
    m.add_function(wrap_pyfunction!(speak_piper, m)?)?;
    m.add_function(wrap_pyfunction!(synthesize_piper, m)?)?;
    m.add_function(wrap_pyfunction!(stop, m)?)?;
    Ok(())
}

/// Return engine version string.
#[pyfunction]
#[allow(clippy::missing_const_for_fn)] // #[pyfunction] prevents const
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Speak text via espeak-ng native FFI.
///
/// # Arguments
/// * `text` — text to speak
/// * `voice` — espeak-ng voice name (e.g. "pt-BR", "en")
/// * `rate` — speech rate in WPM (80–450, default 175)
/// * `pitch` — pitch (0–99, default 50)
/// * `volume` — volume (0–200, default 100)
#[pyfunction]
#[pyo3(signature = (text, voice="pt-BR", rate=175, pitch=50, volume=100))]
fn speak_espeak(
    text: &str,
    voice: &str,
    rate: i32,
    pitch: i32,
    volume: i32,
) -> PyResult<bool> {
    backends::espeak::speak(text, voice, rate, pitch, volume)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

/// Stop any active speech (espeak + rodio audio).
#[pyfunction]
fn stop() -> PyResult<()> {
    audio::stop_playback();
    backends::espeak::cancel()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

/// Speak text via Piper neural TTS (native ONNX inference).
///
/// # Arguments
/// * `text` — text to speak
/// * `model_path` — path to .onnx model file (corresponding .onnx.json must exist)
/// * `length_scale` — speed: <1.0 = faster, 1.0 = normal, >1.0 = slower (default 1.0)
/// * `noise_scale` — voice expressiveness (default 0.667)
/// * `noise_w` — phoneme width noise (default 0.8)
/// * `volume` — volume multiplier (default 1.0)
#[pyfunction]
#[pyo3(signature = (text, model_path, length_scale=1.0, noise_scale=0.667, noise_w=0.8, volume=1.0))]
fn speak_piper(
    text: &str,
    model_path: &str,
    length_scale: f32,
    noise_scale: f32,
    noise_w: f32,
    volume: f32,
) -> PyResult<bool> {
    backends::piper::speak(text, model_path, length_scale, noise_scale, noise_w, volume)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

/// Synthesize text to WAV bytes via Piper (no playback).
///
/// Returns raw WAV file bytes. Useful for saving to file or streaming.
#[pyfunction]
#[pyo3(signature = (text, model_path, length_scale=1.0, noise_scale=0.667, noise_w=0.8, volume=1.0))]
fn synthesize_piper<'py>(
    py: Python<'py>,
    text: &str,
    model_path: &str,
    length_scale: f32,
    noise_scale: f32,
    noise_w: f32,
    volume: f32,
) -> PyResult<Bound<'py, pyo3::types::PyBytes>> {
    let wav = backends::piper::synthesize(text, model_path, length_scale, noise_scale, noise_w, volume)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok(pyo3::types::PyBytes::new(py, &wav))
}
