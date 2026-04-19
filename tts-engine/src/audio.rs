//! Audio playback via rodio (PipeWire/PulseAudio/ALSA auto-detected).
//!
//! Uses a dedicated audio thread since `OutputStream` is `!Send + !Sync`.

use std::io::Cursor;
use std::sync::mpsc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;

use rodio::{Decoder, OutputStream, Sink};

use crate::error::{Result, TtsError};

/// Shared stop flag — set to true to interrupt playback.
static STOP_FLAG: AtomicBool = AtomicBool::new(false);

/// Play raw WAV data from memory. Blocks until playback finishes or `stop()` is called.
pub fn play_wav(wav_data: Vec<u8>) -> Result<()> {
    STOP_FLAG.store(false, Ordering::SeqCst);

    let (tx, rx) = mpsc::channel::<std::result::Result<(), String>>();

    thread::spawn(move || {
        let result = (|| -> std::result::Result<(), String> {
            let (_stream, handle) =
                OutputStream::try_default().map_err(|e| e.to_string())?;
            let sink = Sink::try_new(&handle).map_err(|e| e.to_string())?;

            let cursor = Cursor::new(wav_data);
            let source = Decoder::new(cursor).map_err(|e| e.to_string())?;
            sink.append(source);

            // Poll until done or stopped
            while !sink.empty() {
                if STOP_FLAG.load(Ordering::SeqCst) {
                    sink.stop();
                    return Ok(());
                }
                thread::sleep(std::time::Duration::from_millis(50));
            }
            Ok(())
        })();

        let _ = tx.send(result);
    });

    rx.recv()
        .map_err(|e| TtsError::Audio(e.to_string()))?
        .map_err(TtsError::Audio)
}

/// Stop any active playback.
pub fn stop_playback() {
    STOP_FLAG.store(true, Ordering::SeqCst);
}

