#![allow(unsafe_code)]
//! Piper neural TTS backend — ONNX inference via `ort` + espeak-ng phonemization.
//!
//! Pipeline: text → espeak-ng IPA phonemes → phoneme IDs → ONNX model → audio → rodio
//!
//! Model sessions are cached to avoid reloading the ONNX model on every call.

use std::collections::HashMap;
use std::ffi::{CStr, CString};
use std::path::Path;
use std::sync::Mutex;

use crate::error::{Result, TtsError};

// ── espeak-ng FFI for phonemization ─────────────────────────────────────────

const ESPEAK_CHARS_UTF8: i32 = 1;
const ESPEAK_PHONEMES_IPA: i32 = 0x02;

unsafe extern "C" {
    fn espeak_TextToPhonemes(
        textptr: *mut *const std::ffi::c_void,
        textmode: i32,
        phonememode: i32,
    ) -> *const std::ffi::c_char;
}

// ── Model config (from .onnx.json) ─────────────────────────────────────────

#[derive(serde::Deserialize)]
#[allow(dead_code)]
struct PiperConfig {
    audio: AudioConfig,
    espeak: EspeakConfig,
    inference: InferenceConfig,
    phoneme_id_map: HashMap<String, Vec<i64>>,
    #[serde(default)]
    phoneme_map: HashMap<String, Vec<String>>,
}

#[derive(serde::Deserialize)]
struct AudioConfig {
    sample_rate: u32,
}

#[derive(serde::Deserialize)]
struct EspeakConfig {
    voice: String,
}

#[derive(serde::Deserialize)]
#[allow(dead_code)]
struct InferenceConfig {
    noise_scale: f32,
    length_scale: f32,
    noise_w: f32,
}

// ── Model cache ─────────────────────────────────────────────────────────────

struct CachedModel {
    path: String,
    session: ort::session::Session,
    config: PiperConfig,
}

static MODEL_CACHE: Mutex<Option<CachedModel>> = Mutex::new(None);

fn get_or_load_model(model_path: &str) -> Result<std::sync::MutexGuard<'static, Option<CachedModel>>> {
    let mut cache = MODEL_CACHE.lock().map_err(|e| TtsError::Onnx(e.to_string()))?;

    let needs_load = (*cache).as_ref().is_none_or(|cached| cached.path != model_path);

    if needs_load {
        let model = Path::new(model_path);
        if !model.exists() {
            return Err(TtsError::Model(format!("model not found: {model_path}")));
        }

        let config_path = format!("{model_path}.json");
        let config_str = std::fs::read_to_string(&config_path)
            .map_err(|e| TtsError::Model(format!("config not found: {config_path}: {e}")))?;
        let config: PiperConfig = serde_json::from_str(&config_str)
            .map_err(|e| TtsError::Model(format!("invalid config: {e}")))?;

        let session = ort::session::Session::builder()
            .map_err(|e| TtsError::Onnx(e.to_string()))?
            .with_intra_threads(2)
            .map_err(|e| TtsError::Onnx(e.to_string()))?
            .commit_from_file(model_path)
            .map_err(|e| TtsError::Onnx(e.to_string()))?;

        *cache = Some(CachedModel {
            path: model_path.to_string(),
            session,
            config,
        });
    }

    Ok(cache)
}

// ── Phonemization ───────────────────────────────────────────────────────────

fn text_to_phonemes(text: &str, voice: &str) -> Result<String> {
    let voice_c = CString::new(voice).map_err(|_| TtsError::Espeak(-1))?;
    let err = unsafe { super::espeak::set_voice_raw(voice_c.as_ptr()) };
    if err != 0 {
        return Err(TtsError::Espeak(err));
    }

    let text_c = CString::new(text).map_err(|_| TtsError::Espeak(-1))?;
    let mut text_ptr: *const std::ffi::c_void = text_c.as_ptr().cast();
    let mut result = String::new();

    unsafe {
        loop {
            let phonemes = espeak_TextToPhonemes(
                &raw mut text_ptr,
                ESPEAK_CHARS_UTF8,
                ESPEAK_PHONEMES_IPA,
            );

            if phonemes.is_null() {
                break;
            }

            let phoneme_str = CStr::from_ptr(phonemes)
                .to_str()
                .map_err(|_| TtsError::Espeak(-2))?;

            if !result.is_empty() && !phoneme_str.is_empty() {
                result.push(' ');
            }
            result.push_str(phoneme_str);

            if text_ptr.is_null() {
                break;
            }
        }
    }

    Ok(result)
}

// ── Phoneme → ID mapping ───────────────────────────────────────────────────

fn phonemes_to_ids(
    phonemes: &str,
    id_map: &HashMap<String, Vec<i64>>,
    phoneme_map: &HashMap<String, Vec<String>>,
) -> Vec<i64> {
    let pad_id = id_map.get("_").and_then(|v| v.first().copied()).unwrap_or(0);
    let bos_id = id_map.get("^").and_then(|v| v.first().copied()).unwrap_or(1);
    let eos_id = id_map.get("$").and_then(|v| v.first().copied()).unwrap_or(2);

    let mut ids = Vec::with_capacity(phonemes.len() * 3);
    ids.push(bos_id);

    let decomposed = unicode_normalization_nfd(phonemes);

    for ch in decomposed.chars() {
        let ch_str = ch.to_string();

        let mapped = phoneme_map
            .get(&ch_str)
            .map_or_else(|| std::slice::from_ref(&ch_str), Vec::as_slice);

        for m in mapped {
            if let Some(pid_list) = id_map.get(m.as_str()) {
                for &pid in pid_list {
                    ids.push(pid);
                    ids.push(pad_id);
                }
            }
        }
    }

    ids.push(eos_id);
    ids
}

/// Manual NFD for IPA characters espeak-ng produces.
fn unicode_normalization_nfd(s: &str) -> String {
    let mut result = String::with_capacity(s.len() * 2);
    for ch in s.chars() {
        match ch {
            '\u{00E3}' => { result.push('a'); result.push('\u{0303}'); }
            '\u{1EBD}' => { result.push('e'); result.push('\u{0303}'); }
            '\u{0129}' => { result.push('i'); result.push('\u{0303}'); }
            '\u{00F5}' => { result.push('o'); result.push('\u{0303}'); }
            '\u{0169}' => { result.push('u'); result.push('\u{0303}'); }
            '\u{00E1}' => { result.push('a'); result.push('\u{0301}'); }
            '\u{00E9}' => { result.push('e'); result.push('\u{0301}'); }
            '\u{00ED}' => { result.push('i'); result.push('\u{0301}'); }
            '\u{00F3}' => { result.push('o'); result.push('\u{0301}'); }
            '\u{00FA}' => { result.push('u'); result.push('\u{0301}'); }
            '\u{00FD}' => { result.push('y'); result.push('\u{0301}'); }
            '\u{00E0}' => { result.push('a'); result.push('\u{0300}'); }
            '\u{00E8}' => { result.push('e'); result.push('\u{0300}'); }
            '\u{00EC}' => { result.push('i'); result.push('\u{0300}'); }
            '\u{00F2}' => { result.push('o'); result.push('\u{0300}'); }
            '\u{00F9}' => { result.push('u'); result.push('\u{0300}'); }
            '\u{00E4}' => { result.push('a'); result.push('\u{0308}'); }
            '\u{00F6}' => { result.push('o'); result.push('\u{0308}'); }
            '\u{00FC}' => { result.push('u'); result.push('\u{0308}'); }
            '\u{00E2}' => { result.push('a'); result.push('\u{0302}'); }
            '\u{00EA}' => { result.push('e'); result.push('\u{0302}'); }
            '\u{00EE}' => { result.push('i'); result.push('\u{0302}'); }
            '\u{00F4}' => { result.push('o'); result.push('\u{0302}'); }
            '\u{00FB}' => { result.push('u'); result.push('\u{0302}'); }
            _ => result.push(ch),
        }
    }
    result
}

// ── ONNX inference ──────────────────────────────────────────────────────────

fn infer(
    session: &mut ort::session::Session,
    phoneme_ids: &[i64],
    noise_scale: f32,
    length_scale: f32,
    noise_w: f32,
) -> Result<Vec<f32>> {
    let seq_len = phoneme_ids.len();

    let input_tensor = ort::value::Value::from_array(
        ([1, seq_len], phoneme_ids.to_vec()),
    )
    .map_err(|e| TtsError::Onnx(e.to_string()))?;

    #[allow(clippy::cast_possible_wrap)] // seq_len always < i64::MAX
    let lengths_tensor = ort::value::Value::from_array(
        ([1_usize], vec![seq_len as i64]),
    )
    .map_err(|e| TtsError::Onnx(e.to_string()))?;

    let scales_tensor = ort::value::Value::from_array(
        ([3_usize], vec![noise_scale, length_scale, noise_w]),
    )
    .map_err(|e| TtsError::Onnx(e.to_string()))?;

    let outputs = session
        .run(
            ort::inputs![
                "input" => input_tensor,
                "input_lengths" => lengths_tensor,
                "scales" => scales_tensor,
            ],
        )
        .map_err(|e| TtsError::Onnx(e.to_string()))?;

    let output = outputs
        .get("output")
        .ok_or_else(|| TtsError::Onnx("missing output tensor".into()))?;

    let audio_data = output
        .try_extract_tensor::<f32>()
        .map_err(|e| TtsError::Onnx(e.to_string()))?;

    Ok(audio_data.1.to_vec())
}

fn samples_to_wav(samples: &[f32], sample_rate: u32) -> Result<Vec<u8>> {
    let mut cursor = std::io::Cursor::new(Vec::new());
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };

    let mut writer = hound::WavWriter::new(&mut cursor, spec)
        .map_err(|e| TtsError::Audio(e.to_string()))?;

    for &sample in samples {
        let clamped = sample.clamp(-1.0, 1.0);
        #[allow(clippy::cast_possible_truncation)] // intentional f32→i16 audio conversion
        let pcm = (clamped * 32767.0) as i16;
        writer
            .write_sample(pcm)
            .map_err(|e| TtsError::Audio(e.to_string()))?;
    }

    writer
        .finalize()
        .map_err(|e| TtsError::Audio(e.to_string()))?;

    Ok(cursor.into_inner())
}

// ── Public API ──────────────────────────────────────────────────────────────

/// Synthesize text to WAV bytes (no playback).
pub fn synthesize(
    text: &str,
    model_path: &str,
    length_scale: f32,
    noise_scale: f32,
    noise_w: f32,
    volume_factor: f32,
) -> Result<Vec<u8>> {
    super::espeak::ensure_init_public()?;

    let mut cache = get_or_load_model(model_path)?;
    let cached = cache.as_mut().ok_or_else(|| TtsError::Onnx("cache empty".into()))?;

    let phonemes = text_to_phonemes(text, &cached.config.espeak.voice)?;
    if phonemes.is_empty() {
        return Ok(Vec::new());
    }

    let ids = phonemes_to_ids(&phonemes, &cached.config.phoneme_id_map, &cached.config.phoneme_map);

    let audio = infer(
        &mut cached.session,
        &ids,
        noise_scale,
        length_scale,
        noise_w,
    )?;

    let sample_rate = cached.config.audio.sample_rate;
    drop(cache); // Release mutex before WAV encoding

    let audio: Vec<f32> = if (volume_factor - 1.0).abs() > 0.01 {
        audio.iter().map(|&s| s * volume_factor).collect()
    } else {
        audio
    };

    samples_to_wav(&audio, sample_rate)
}

/// Speak text using Piper neural TTS (synthesize + play).
pub fn speak(
    text: &str,
    model_path: &str,
    length_scale: f32,
    noise_scale: f32,
    noise_w: f32,
    volume_factor: f32,
) -> Result<bool> {
    let wav = synthesize(text, model_path, length_scale, noise_scale, noise_w, volume_factor)?;
    if wav.is_empty() {
        return Ok(false);
    }
    crate::audio::play_wav(wav)?;
    Ok(true)
}
