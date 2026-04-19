//! espeak-ng native backend via direct FFI to libespeak-ng.so.
//!
//! Eliminates subprocess overhead — calls espeak-ng API directly.
//! Uses `AUDIO_OUTPUT_PLAYBACK` mode: espeak-ng handles audio output internally.

use std::ffi::CString;
use std::sync::OnceLock;

use crate::error::{Result, TtsError};

// ── FFI declarations ────────────────────────────────────────────────────────

// espeak_AUDIO_OUTPUT enum values
const AUDIO_OUTPUT_PLAYBACK: i32 = 0;

// espeak_PARAMETER enum values
const ESPEAK_RATE: i32 = 1;
const ESPEAK_VOLUME: i32 = 2;
const ESPEAK_PITCH: i32 = 3;

// espeak_ERROR enum values
const EE_OK: i32 = 0;

// espeak_Initialize options
const ESPEAK_INITIALIZE_DONT_EXIT: i32 = 0x8000;

// espeakSSML flag for espeak_Synth
const ESPEAK_CHARS_AUTO: u32 = 0;

unsafe extern "C" {
    fn espeak_Initialize(
        output: i32,
        buflength: i32,
        path: *const std::ffi::c_char,
        options: i32,
    ) -> i32;

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

    fn espeak_Synchronize() -> i32;
}

// ── Initialization ──────────────────────────────────────────────────────────

/// Stores `espeak_Initialize` result (sample rate on success, negative on error).
static INIT_RESULT: OnceLock<i32> = OnceLock::new();

fn ensure_init() -> Result<()> {
    let &result = INIT_RESULT.get_or_init(|| {
        // SAFETY: espeak_Initialize is called once, globally, before any other
        // espeak function. AUDIO_OUTPUT_PLAYBACK lets espeak handle audio.
        // DONT_EXIT prevents espeak from calling exit() on fatal errors.
        unsafe {
            espeak_Initialize(
                AUDIO_OUTPUT_PLAYBACK,
                0, // default buffer length
                std::ptr::null(),
                ESPEAK_INITIALIZE_DONT_EXIT,
            )
        }
    });

    if result < 0 {
        return Err(TtsError::EspeakInit(format!(
            "espeak_Initialize returned {result}"
        )));
    }
    Ok(())
}

// ── Public API ──────────────────────────────────────────────────────────────

/// Speak text using espeak-ng native API.
///
/// This function is synchronous: it returns after synthesis + playback complete.
///
/// # Arguments
/// * `text` — UTF-8 text to speak
/// * `voice` — espeak-ng voice name (e.g. "pt-BR", "en", "de")
/// * `rate` — speaking rate in WPM (80–450)
/// * `pitch` — base pitch (0–99, 50 = normal)
/// * `volume` — volume (0–200, 100 = normal)
pub fn speak(text: &str, voice: &str, rate: i32, pitch: i32, volume: i32) -> Result<bool> {
    ensure_init()?;

    let voice_c = CString::new(voice)
        .map_err(|_| TtsError::Espeak(-1))?;
    let text_c = CString::new(text)
        .map_err(|_| TtsError::Espeak(-1))?;

    // SAFETY: espeak-ng is initialized. CStrings are valid null-terminated.
    // All parameters are within documented ranges.
    unsafe {
        let err = espeak_SetVoiceByName(voice_c.as_ptr());
        if err != EE_OK {
            return Err(TtsError::Espeak(err));
        }

        // Clamp parameters to espeak-ng documented ranges
        let rate_clamped = rate.clamp(80, 450);
        let pitch_clamped = pitch.clamp(0, 99);
        let volume_clamped = volume.clamp(0, 200);

        espeak_SetParameter(ESPEAK_RATE, rate_clamped, 0);
        espeak_SetParameter(ESPEAK_PITCH, pitch_clamped, 0);
        espeak_SetParameter(ESPEAK_VOLUME, volume_clamped, 0);

        let text_bytes = text_c.as_bytes_with_nul();
        let err = espeak_Synth(
            text_bytes.as_ptr().cast(),
            text_bytes.len(),
            0,                   // position
            0,                   // position_type: POS_CHARACTER
            0,                   // end_position: 0 = end of text
            ESPEAK_CHARS_AUTO,   // flags
            std::ptr::null_mut(),// unique_identifier
            std::ptr::null_mut(),// user_data
        );
        if err != EE_OK {
            return Err(TtsError::Espeak(err));
        }

        // Wait for playback to finish (espeak plays asynchronously internally)
        espeak_Synchronize();
    }

    Ok(true)
}

/// Expose initialization for other backends (e.g. Piper phonemization).
pub fn ensure_init_public() -> Result<()> {
    ensure_init()
}

/// Set voice by name — raw C string version for use by piper backend.
///
/// # Safety
/// `name` must point to a valid null-terminated C string.
pub unsafe fn set_voice_raw(name: *const std::ffi::c_char) -> i32 {
    unsafe { espeak_SetVoiceByName(name) }
}

/// Cancel any active espeak-ng synthesis.
pub fn cancel() -> Result<()> {
    ensure_init()?;
    // SAFETY: espeak-ng is initialized
    let err = unsafe { espeak_Cancel() };
    if err != EE_OK {
        return Err(TtsError::Espeak(err));
    }
    Ok(())
}

/// Get the sample rate espeak-ng was initialized with.
/// Returns the sample rate in Hz, or an error.
#[allow(dead_code)]
pub fn sample_rate() -> Result<i32> {
    ensure_init()?;
    let &rate = INIT_RESULT.get().ok_or_else(|| TtsError::EspeakInit("not initialized".into()))?;
    if rate > 0 {
        Ok(rate)
    } else {
        Err(TtsError::EspeakInit("not initialized".into()))
    }
}
