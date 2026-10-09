//! TTS backend modules.

// The FFI boundary to libespeak-ng: `unsafe` cannot be avoided there. Every
// block carries a SAFETY comment and all calls are serialized by ESPEAK_LOCK.
#[allow(unsafe_code)]
pub mod espeak;
pub mod piper;
