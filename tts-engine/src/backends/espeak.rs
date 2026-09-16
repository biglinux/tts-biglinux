//! espeak-ng native backend via direct FFI to libespeak-ng.so.
//!
//! Eliminates subprocess overhead — calls espeak-ng API directly.
//!
//! IMPORTANT DESIGN NOTE (regression guard for the "English intro" bug):
//! espeak-ng is initialized in `AUDIO_OUTPUT_SYNCHRONOUS` mode, **never**
//! `AUDIO_OUTPUT_PLAYBACK`. In synchronous mode espeak-ng never opens the
//! audio device and never plays on its own — it only hands raw PCM samples to
//! our synth callback. This guarantees that when Piper (or anything else) uses
//! espeak-ng purely as a *phonemizer* (`phonemize()` below), espeak can never
//! emit a stray/default (English) utterance. All audible espeak output is
//! rendered to PCM here and played through the centralized `crate::audio`
//! engine, which gives us unified cancellation and volume control.
//!
//! All espeak-ng FFI is serialized through `ESPEAK_LOCK` because libespeak-ng
//! keeps global translator state and is not thread-safe.

use std::ffi::{CStr, CString};
use std::sync::{Mutex, OnceLock};

use crate::error::{Result, TtsError};

// ── FFI declarations ────────────────────────────────────────────────────────

// espeak_AUDIO_OUTPUT enum values
const AUDIO_OUTPUT_SYNCHRONOUS: i32 = 2;

// espeak_PARAMETER enum values
const ESPEAK_RATE: i32 = 1;
const ESPEAK_VOLUME: i32 = 2;
const ESPEAK_PITCH: i32 = 3;

// espeak_ERROR enum values
const EE_OK: i32 = 0;

// espeak_Initialize options
const ESPEAK_INITIALIZE_DONT_EXIT: i32 = 0x8000;

// espeak_Synth flags
const ESPEAK_CHARS_AUTO: u32 = 0;
const ESPEAK_CHARS_UTF8: i32 = 1;

// espeak_TextToPhonemes phoneme mode: bit 1 = output IPA
const ESPEAK_PHONEMES_IPA: i32 = 0x02;

/// espeak synth callback: `wav` points to `numsamples` i16 samples, or is null
/// at end-of-stream. Returning non-zero aborts synthesis.
type SynthCallback =
    unsafe extern "C" fn(*mut i16, i32, *mut std::ffi::c_void) -> i32;

unsafe extern "C" {
    fn espeak_Initialize(
        output: i32,
        buflength: i32,
        path: *const std::ffi::c_char,
        options: i32,
    ) -> i32;

    fn espeak_SetSynthCallback(callback: Option<SynthCallback>);

    fn espeak_SetVoiceByName(name: *const std::ffi::c_char) -> i32;

    fn espeak_SetParameter(parameter: i32, value: i32, relative: i32) -> i32;

    fn espeak_Synth(
        text: *const std::ffi::c_void,
        size: usize,
        position: u32,
        position_type: i32,
        end_position: u32,
        flags: u32,
        unique_identifier: *mut u32,
        user_data: *mut std::ffi::c_void,
    ) -> i32;

    fn espeak_Cancel() -> i32;

    fn espeak_TextToPhonemes(
        textptr: *mut *const std::ffi::c_void,
        textmode: i32,
        phonememode: i32,
    ) -> *const std::ffi::c_char;
}

// ── Global state ──────────────────────────────────────────────────────────────

/// Serializes all espeak-ng FFI calls (libespeak-ng is not thread-safe).
static ESPEAK_LOCK: Mutex<()> = Mutex::new(());

/// Collects PCM samples emitted by the synth callback for the current synthesis.
static SAMPLE_BUFFER: Mutex<Vec<i16>> = Mutex::new(Vec::new());

/// Stores `espeak_Initialize` result (sample rate on success, negative on error).
static INIT_RESULT: OnceLock<i32> = OnceLock::new();

/// Synth callback — appends samples to `SAMPLE_BUFFER`. Never plays audio.
///
/// # Safety
/// Called by espeak-ng from within `espeak_Synth`. `wav` is valid for
/// `numsamples` i16 elements when non-null.
unsafe extern "C" fn synth_callback(
    wav: *mut i16,
    numsamples: i32,
    _events: *mut std::ffi::c_void,
) -> i32 {
    if !wav.is_null() && numsamples > 0 {
        // SAFETY: espeak guarantees `wav` holds `numsamples` i16 samples.
        let slice = unsafe {
            std::slice::from_raw_parts(wav, numsamples as usize)
        };
        if let Ok(mut buf) = SAMPLE_BUFFER.lock() {
            buf.extend_from_slice(slice);
        }
    }
    0 // continue synthesis
}

fn ensure_init() -> Result<()> {
    let &result = INIT_RESULT.get_or_init(|| {
        // SAFETY: espeak_Initialize is called once, globally, before any other
        // espeak function. SYNCHRONOUS mode means espeak never touches the audio
        // device; DONT_EXIT prevents espeak from calling exit() on fatal errors.
        let rate = unsafe {
            espeak_Initialize(
                AUDIO_OUTPUT_SYNCHRONOUS,
                0, // default buffer length
                std::ptr::null(),
                ESPEAK_INITIALIZE_DONT_EXIT,
            )
        };
        if rate >= 0 {
            // SAFETY: espeak is initialized; registering our sample sink.
            unsafe { espeak_SetSynthCallback(Some(synth_callback)) };
        }
        rate
    });

    if result < 0 {
        return Err(TtsError::EspeakInit(format!(
            "espeak_Initialize returned {result}"
        )));
    }
    Ok(())
}

// ── Sample → WAV ──────────────────────────────────────────────────────────────

fn samples_to_wav(samples: &[i16], sample_rate: u32) -> Result<Vec<u8>> {
    let mut cursor = std::io::Cursor::new(Vec::new());
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer = hound::WavWriter::new(&mut cursor, spec)
        .map_err(|e| TtsError::Audio(e.to_string()))?;
    for &s in samples {
        writer.write_sample(s).map_err(|e| TtsError::Audio(e.to_string()))?;
    }
    writer.finalize().map_err(|e| TtsError::Audio(e.to_string()))?;
    Ok(cursor.into_inner())
}

// ── Public API ──────────────────────────────────────────────────────────────

/// Synthesize `text` with espeak-ng into WAV bytes (no playback).
///
/// Returns an empty vector if espeak produced no audio.
pub fn synthesize(
    text: &str,
    voice: &str,
    rate: i32,
    pitch: i32,
    volume: i32,
) -> Result<Vec<u8>> {
    let _guard = ESPEAK_LOCK.lock().map_err(|e| TtsError::EspeakInit(e.to_string()))?;
    ensure_init()?;

    let voice_c = CString::new(voice).map_err(|_| TtsError::Espeak(-1))?;
    let text_c = CString::new(text).map_err(|_| TtsError::Espeak(-1))?;

    let sample_rate = INIT_RESULT.get().copied().unwrap_or(22050).max(1) as u32;

    // SAFETY: espeak is initialized and access is serialized by ESPEAK_LOCK.
    // CStrings are valid null-terminated; parameters are clamped below.
    unsafe {
        let err = espeak_SetVoiceByName(voice_c.as_ptr());
        if err != EE_OK {
            return Err(TtsError::Espeak(err));
        }

        espeak_SetParameter(ESPEAK_RATE, rate.clamp(80, 450), 0);
        espeak_SetParameter(ESPEAK_PITCH, pitch.clamp(0, 99), 0);
        // volume 0 → silent (true mute); espeak accepts 0..200.
        espeak_SetParameter(ESPEAK_VOLUME, volume.clamp(0, 200), 0);

        // Reset the sample sink for this synthesis.
        if let Ok(mut buf) = SAMPLE_BUFFER.lock() {
            buf.clear();
        }

        let text_bytes = text_c.as_bytes_with_nul();
        let err = espeak_Synth(
            text_bytes.as_ptr().cast(),
            text_bytes.len(),
            0,
            0,
            0,
            ESPEAK_CHARS_AUTO,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        );
        if err != EE_OK {
            return Err(TtsError::Espeak(err));
        }
    }

    let samples = SAMPLE_BUFFER
        .lock()
        .map(|b| b.clone())
        .unwrap_or_default();
    if samples.is_empty() {
        return Ok(Vec::new());
    }
    samples_to_wav(&samples, sample_rate)
}

/// Speak `text` using espeak-ng: synthesize to PCM, then play through the
/// centralized rodio audio engine. Blocks until playback finishes or `cancel()`.
pub fn speak(text: &str, voice: &str, rate: i32, pitch: i32, volume: i32) -> Result<bool> {
    // volume 0 == mute: nothing audible.
    if volume <= 0 {
        return Ok(true);
    }
    let wav = synthesize(text, voice, rate, pitch, volume)?;
    if wav.is_empty() {
        return Ok(false);
    }
    crate::audio::play_wav(wav)?;
    Ok(true)
}

/// Expose initialization for other backends (e.g. Piper phonemization).
pub fn ensure_init_public() -> Result<()> {
    let _guard = ESPEAK_LOCK.lock().map_err(|e| TtsError::EspeakInit(e.to_string()))?;
    ensure_init()
}

/// Phonemize `text` to IPA using espeak-ng — used by the Piper backend.
///
/// This never produces audio: espeak is in synchronous mode and only
/// `espeak_TextToPhonemes` is invoked.
pub fn phonemize(text: &str, voice: &str) -> Result<String> {
    let _guard = ESPEAK_LOCK.lock().map_err(|e| TtsError::EspeakInit(e.to_string()))?;
    ensure_init()?;

    let voice_c = CString::new(voice).map_err(|_| TtsError::Espeak(-1))?;
    // SAFETY: espeak initialized; access serialized by ESPEAK_LOCK.
    let err = unsafe { espeak_SetVoiceByName(voice_c.as_ptr()) };
    if err != EE_OK {
        return Err(TtsError::Espeak(err));
    }

    let text_c = CString::new(text).map_err(|_| TtsError::Espeak(-1))?;
    let mut text_ptr: *const std::ffi::c_void = text_c.as_ptr().cast();
    let mut result = String::new();

    // SAFETY: espeak initialized; text_ptr is a valid null-terminated buffer
    // that espeak advances through clause by clause until it becomes null.
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

/// Cancel any active espeak-ng synthesis and stop playback.
///
/// Safe to call from another thread while `speak()` is running: it does not
/// take `ESPEAK_LOCK` (which the in-flight synthesis holds) — `espeak_Cancel`
/// is designed to be called concurrently.
pub fn cancel() -> Result<()> {
    crate::audio::stop_playback();
    // SAFETY: espeak_Cancel is thread-safe by espeak-ng's contract.
    let err = unsafe { espeak_Cancel() };
    if err != EE_OK {
        return Err(TtsError::Espeak(err));
    }
    Ok(())
}

/// Get the sample rate espeak-ng was initialized with.
#[allow(dead_code)]
pub fn sample_rate() -> Result<i32> {
    ensure_init_public()?;
    let &rate = INIT_RESULT.get().ok_or_else(|| TtsError::EspeakInit("not initialized".into()))?;
    if rate > 0 {
        Ok(rate)
    } else {
        Err(TtsError::EspeakInit("not initialized".into()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phonemize_pt_br_is_nonempty() {
        // Regression guard for the "English intro" bug: phonemization works and
        // (implicitly) never emits audio — espeak is in synchronous mode.
        let p = phonemize("Bom dia", "pt-br").expect("phonemize should succeed");
        assert!(!p.is_empty(), "expected non-empty phonemes for pt-br");
    }

    #[test]
    fn synth_volume_zero_is_true_mute() {
        let wav = synthesize("Bom dia", "pt-br", 175, 50, 0).expect("synth should succeed");
        if !wav.is_empty() {
            // Every PCM sample (after the 44-byte WAV header) must be silent.
            assert!(
                wav[44..].iter().all(|&b| b == 0),
                "volume 0 must produce silent PCM"
            );
        }
    }

    #[test]
    fn synth_volume_full_produces_audio() {
        let wav = synthesize("Bom dia", "pt-br", 175, 50, 100).expect("synth should succeed");
        assert!(wav.len() > 44, "expected non-empty WAV at full volume");
        assert!(
            wav[44..].iter().any(|&b| b != 0),
            "expected audible PCM at volume 100"
        );
    }
}
