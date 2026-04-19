//! Error types for tts-engine.

/// Unified error type for all TTS engine operations.
#[derive(Debug, thiserror::Error)]
pub enum TtsError {
    /// espeak-ng FFI returned an error code.
    #[error("espeak-ng error code {0}")]
    Espeak(i32),
    /// espeak-ng initialization failed.
    #[error("espeak-ng init failed: {0}")]
    EspeakInit(String),
    /// Audio playback failed.
    #[error("audio playback error: {0}")]
    Audio(String),
    /// ONNX inference failed.
    #[error("ONNX inference error: {0}")]
    Onnx(String),
    /// Model file not found or invalid.
    #[error("model error: {0}")]
    Model(String),
}

pub type Result<T> = std::result::Result<T, TtsError>;
