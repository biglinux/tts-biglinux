# BigLinux TTS

<p align="center">
  <img src="usr/share/icons/hicolor/scalable/apps/biglinux-tts.svg" alt="BigLinux TTS" width="128">
</p>

<p align="center">
  <strong>Complete text-to-speech solution with native GUI for Linux desktop</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/version-4.0.0-brightgreen.svg" alt="Version">
  <img src="https://img.shields.io/badge/GTK-4-green.svg" alt="GTK4">
  <img src="https://img.shields.io/badge/libadwaita-1.x-purple.svg" alt="libadwaita">
  <img src="https://img.shields.io/badge/Rust-1.85+-orange.svg" alt="Rust">
  <img src="https://img.shields.io/badge/Python-3.10+-yellow.svg" alt="Python">
  <img src="https://img.shields.io/badge/engines-4-red.svg" alt="4 TTS engines">
  <img src="https://img.shields.io/badge/languages-29-lightgrey.svg" alt="29 languages">
</p>

---

## Table of Contents

- [About](#about)
- [History](#history)
- [Features](#features)
- [TTS Engines](#tts-engines)
- [Architecture](#architecture)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Internationalization](#internationalization)
- [Technical Details](#technical-details)
- [Building from Source](#building-from-source)
- [License](#license)
- [Authors](#authors)

---

## About

**BigLinux TTS** is a native desktop Linux application that converts text to speech. Built with GTK4, libadwaita, and a native Rust audio engine, it is the built-in screen reader for [BigLinux](https://www.biglinux.com.br/) — a Brazilian Linux distribution based on Manjaro/Arch Linux.

Select any text on screen, press **Alt+V**, and hear it read aloud. Press again to stop. No complicated setup.

### Use Cases

- **Accessibility** — screen reading for users with visual impairments or reading difficulties
- **Multitasking** — listen to articles, documents, and emails while doing other things
- **Language learning** — hear correct pronunciation in 100+ languages
- **Proofreading** — catch writing errors by listening to what was written
- **Productivity** — convert passive reading into active listening

### What Sets It Apart

1. **4 TTS engines** — RHVoice, espeak-ng native FFI, Piper Neural TTS, and Kokoro Neural TTS
2. **Native Rust audio** — espeak-ng via direct FFI and Piper ONNX inference via `ort`, no subprocess overhead
3. **Automatic voice discovery** — scans all installed engines and voices system-wide
4. **Smart text processing** — expands abbreviations, pronounces special characters, strips HTML/Markdown
5. **KDE Plasma integration** — global hotkey, system tray icon, launcher pinning
6. **Modern UI** — GTK4 + libadwaita (GNOME HIG), clean and responsive interface
7. **29 languages** — gettext-based i18n with `.po` files

---

## History

BigLinux TTS was born from a practical need: making text-to-speech accessible and easy on Linux desktop.

| Date | Version | Milestone |
|------|---------|-----------|
| Sep 2021 | — | First commit by Bruno Gonçalves: initial web-based interface |
| Mar 2022 | — | Rafael Ruscher joins: icon design, CSS refinements, translations |
| Aug 2022 | — | PKGBUILD packaging, i18n with 29 locales, CI/CD workflow |
| Dec 2023 | — | Volume/pitch/rate range inputs, UI polish |
| Feb 2026 | 3.0 | **Full rewrite**: web UI → GTK4 + libadwaita + Python. speech-dispatcher integration, Piper Neural TTS, tray icon (PySide6 subprocess), text processor with abbreviation expansion |
| Mar 2026 | 3.1 | Native RHVoice backend, parallel voice discovery, Python DBus launcher |
| Mar 2026 | 3.2 | Voice Manager dialog with install/remove, theme support, khotkeys sync |
| Jun 2026 | **4.0** | **Native Rust engine** (PyO3): espeak-ng FFI (zero-subprocess latency), Piper ONNX inference via `ort` with model caching (7× faster short text). Kokoro Neural TTS integration. Complete i18n audit (212 strings). Full codebase cleanup. |

---

## Features

### Text Reading

- **Configurable global hotkey** (default Alt+V) — select text anywhere, press to speak, press again to stop (toggle)
- **System tray icon** — left-click to speak, right-click for menu (Read text, Settings, Quit)
- **Built-in voice test** — text field to type and hear with current voice settings
- **Launcher pinning** — option to pin the speak button to KDE Plasma taskbar

### Voice Control

- **Speed** — scale from -100 (slow) to +100 (fast)
- **Pitch** — scale from -100 (low) to +100 (high)
- **Volume** — scale from 0 (mute) to 100 (max)
- **Voice selection** — dynamic list filtered by engine: "Name — Language [Quality]"

### Text Processing

| Feature | Description | Example |
|---------|-------------|---------|
| Expand abbreviations | Converts slang/abbreviations per language | `tb` → "também", `btw` → "by the way" |
| Special characters | Pronounces symbols by name | `#` → "hash", `@` → "at" |
| Strip formatting | Removes HTML tags, Markdown bold/italic/code | `**bold**` → "bold" |
| URL handling | Option to read or skip links | `https://...` → read or skip |
| Character limit | Truncates long text | Unlimited, 1K, 5K, 10K, 50K, 100K |

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Alt+V (default) | Speak/stop selected text (toggle) |
| Ctrl+Q | Quit application |

### System Tray

- PySide6 `QSystemTrayIcon` running in isolated subprocess (avoids GTK/Qt conflicts)
- Left-click: toggle speak/stop
- Right-click: context menu (Read text, Settings, Quit)
- Communicates with main process via JSON lines over stdin/stdout

---

## TTS Engines

### 1. RHVoice (via speech-dispatcher)

High-quality multilingual TTS through the speech-dispatcher daemon.

| Voice | Language | Quality |
|-------|----------|---------|
| Letícia F123 | pt-BR | ★★★★ |
| Evgeniy | English | ★★★★ |
| + others | Multiple | ★★★–★★★★ |

Communication via `speechd.SSIPClient` (SSIP protocol) with automatic daemon restart fallback.

### 2. espeak-ng (Native FFI) ⚡

Direct C FFI to `libespeak-ng.so` — zero subprocess overhead. The Rust engine calls espeak-ng API functions directly via `unsafe extern "C"` bindings, compiled through PyO3.

- `AUDIO_OUTPUT_PLAYBACK` mode: espeak-ng handles audio output internally
- One-time initialization via `OnceLock` (thread-safe, no `static mut`)
- Supports 100+ languages with basic quality

### 3. Piper (Native ONNX Inference) ★★★★★

Neural TTS with near-human speech quality. Runs ONNX models locally via the `ort` crate — no `piper-tts` binary needed for native mode.

**Pipeline**: text → espeak-ng IPA phonemes (FFI) → phoneme IDs → ONNX model → f32 audio → WAV → rodio playback

| Feature | Detail |
|---------|--------|
| Runtime | `ort` 2.0 (ONNX Runtime, system library) |
| Model cache | `Mutex<Option<CachedModel>>` — load once, reuse across calls |
| Phonemization | espeak-ng `TextToPhonemes` via FFI |
| Audio | rodio with `AtomicBool` stop flag |
| Performance | 7× faster than subprocess for short text |

### 4. Kokoro (Neural TTS) ★★★★★

Advanced neural TTS with voice blending and emotion presets. Runs via Python `kokoro` package with PyTorch backend.

- Voice blending: mix two voices with configurable ratio
- Emotion presets: neutral, happy, calm, urgent, narrative
- Per-language code selection: Portuguese, English, Spanish, and more

### Automatic Voice Discovery

The system discovers voices from all engines simultaneously in background threads:

1. **RHVoice**: `spd-say -o rhvoice -L` → parses SSIP names with hardcoded metadata (language, gender). Fallback: scan `/usr/share/RHVoice/voices/` and pacman packages
2. **espeak-ng**: `espeak-ng --voices` → parses tabular output (language code, gender)
3. **Piper**: scans `/usr/share/piper-voices/`, `~/.local/share/piper-voices/` → detects `.onnx` files with `.onnx.json` config
4. **Kokoro**: scans installed voice packs and user-downloaded `.npy` voice files

Result: `VoiceCatalog` with all available voices, filterable by language, engine, and quality.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                           main.py                                   │
│                 CLI args, logging, App.run()                        │
├─────────────────────────────────────────────────────────────────────┤
│                        application.py                               │
│              TTSApplication (Adw.Application)                       │
│         startup → activate → shutdown lifecycle                     │
├──────────────────┬──────────────────┬───────────────────────────────┤
│    UI Layer      │  Service Layer   │  Data Layer                   │
├──────────────────┼──────────────────┼───────────────────────────────┤
│ window.py        │ tts_service.py   │ config.py                     │
│ ├ HeaderBar      │ ├ speak()        │ ├ AppSettings (dataclasses)   │
│ ├ NavigationView │ ├ stop()         │ ├ TTSBackend enum             │
│ └ Toast overlay  │ └ state machine  │ └ load/save JSON              │
│                  │                  │                               │
│ main_view.py     │ voice_manager.py │ settings_service.py           │
│ ├ Hero section   │ └ discover()     │ └ debounced auto-save (500ms) │
│ ├ Voice controls │                  │                               │
│ ├ Text options   │ text_processor.py│                               │
│ ├ Backend select │ ├ abbreviations  │                               │
│ └ Advanced       │ ├ special chars  │                               │
│                  │ └ formatting     │                               │
│ components.py    │                  │                               │
│ └ Widget factory │ clipboard_svc.py │                               │
│                  │ ├ wl-paste       │                               │
│ welcome_dialog.py│ └ xsel           │                               │
│ voice_manager_dlg│                  │                               │
│ history_view.py  │ tray_service.py  │                               │
│ audio_player.py  │ └ PySide6 subproc│                               │
│                  │                  │                               │
│                  │ kokoro_voice_svc  │                               │
│                  │ └ voice download  │                               │
├──────────────────┴──────────────────┴───────────────────────────────┤
│                    tts_engine.so (Rust/PyO3)                        │
│    ┌──────────┐  ┌──────────────┐  ┌─────────────┐                 │
│    │ espeak   │  │ piper (ONNX) │  │ audio       │                 │
│    │ FFI      │  │ ort + cache  │  │ rodio + stop│                 │
│    └──────────┘  └──────────────┘  └─────────────┘                 │
└─────────────────────────────────────────────────────────────────────┘
```

### Rust Native Engine (`tts-engine/`)

```
tts-engine/
├── Cargo.toml          # PyO3, ort, rodio, hound, serde, thiserror
├── build.rs            # Link args: pyo3 + libespeak-ng
└── src/
    ├── lib.rs          # PyO3 module: speak_espeak, speak_piper, synthesize_piper, stop
    ├── audio.rs        # rodio playback with AtomicBool stop flag
    ├── error.rs        # TtsError enum (thiserror derive)
    └── backends/
        ├── espeak.rs   # FFI to libespeak-ng (OnceLock init, SetVoice, Synth, Cancel)
        └── piper.rs    # ONNX pipeline: phonemize → IDs → infer → WAV → play
```

**Key dependencies**: `pyo3` 0.25 · `ort` 2.0 · `rodio` 0.20 · `hound` 3.5 · `thiserror` 2 · `serde` 1

### TTS State Machine

```
         speak()              stop() / error / done
  ┌──────────────┐       ┌────────────────────────┐
  │              ▼       │                        │
  │         ┌────────┐   │   ┌──────────┐         │
  │         │  IDLE  │───┘   │ SPEAKING │─────────┘
  │         └────────┘       └──────────┘
  │              │                │
  │         speak()          error()
  │              │                │
  │         ┌────▼────┐     ┌────▼─────┐
  │         │SPEAKING │     │  ERROR   │
  │         └─────────┘     └──────────┘
  │                              │
  └──────────────────────────────┘
                speak()
```

---

## Installation

### BigLinux / Manjaro / Arch Linux

```bash
# Install from BigLinux repository
sudo pacman -S tts-biglinux

# Optional: RHVoice Portuguese voice
sudo pacman -S rhvoice rhvoice-voice-leticia-f123

# Optional: Piper neural TTS
sudo pacman -S piper-tts-bin piper-voices-pt-BR

# Optional: system tray icon
sudo pacman -S pyside6
```

### Build from Git

```bash
git clone https://github.com/biglinux/tts-biglinux.git
cd tts-biglinux/pkgbuild
makepkg -si
```

### Run without Installing (Development)

```bash
git clone https://github.com/biglinux/tts-biglinux.git
cd tts-biglinux

# Build native Rust engine
cd tts-engine
ORT_LIB_LOCATION=/usr/lib ORT_PREFER_DYNAMIC_LINK=1 cargo build --release
cd ..

# Symlink the .so
ln -sf ../../tts-engine/target/release/libtts_engine.so \
  usr/share/biglinux/tts-biglinux/tts_engine.so

# Run
cd usr/share/biglinux/tts-biglinux
python main.py --debug
```

### Dependencies

#### Required

| Package | Description |
|---------|-------------|
| `python` (3.10+) | Python interpreter |
| `python-gobject` | GTK bindings for Python (PyGObject) |
| `gtk4` | GTK 4 toolkit |
| `libadwaita` | Adwaita widget library (GNOME HIG) |
| `speech-dispatcher` | Speech synthesis daemon |
| `espeak-ng` | Open-source TTS engine + libespeak-ng.so |
| `xsel` | X11 clipboard access (primary selection) |
| `wl-clipboard-rs` | Wayland clipboard access (wl-paste) |
| `alsa-utils` | ALSA audio utilities |
| `onnxruntime` | ONNX Runtime library (for Piper native inference) |

#### Build Dependencies

| Package | Description |
|---------|-------------|
| `rust` (1.85+) | Rust toolchain |
| `cargo` | Rust package manager |

#### Optional

| Package | Description |
|---------|-------------|
| `pyside6` | System tray icon (QSystemTrayIcon subprocess) |
| `rhvoice` | High-quality multilingual TTS engine |
| `rhvoice-voice-leticia-f123` | Brazilian Portuguese female voice |
| `piper-tts-bin` | Piper TTS binary (subprocess fallback) |
| `piper-voices-pt-BR` | Brazilian Portuguese neural voices |
| `python-kokoro` | Kokoro neural TTS engine |
| `python-pytorch` | PyTorch runtime for Kokoro |

---

## Usage

### GUI

```bash
biglinux-tts            # Open settings window
biglinux-tts --debug    # Debug mode with detailed logging
biglinux-tts --version  # Print version
```

### Keyboard Shortcut (CLI)

```bash
biglinux-tts-speak      # Speak selected text (called by Alt+V)
```

The `biglinux-tts-speak` script works as a toggle:
1. Already speaking → stop immediately (kill process via PID file)
2. Text selected → read aloud with configured engine/voice
3. No text → exit silently

### Typical Workflow

1. **First launch**: welcome dialog explains features and setup
2. **Configure**: select TTS engine, voice, adjust speed/pitch/volume
3. **Test**: type text in the test field and click "Test voice"
4. **Daily use**: select text anywhere → Alt+V → listen

---

## Configuration

### File Locations

| Path | Content |
|------|---------|
| `~/.config/biglinux-tts/settings.json` | All app settings (JSON) |
| `/tmp/biglinux-tts-{user}.pid` | Speech process PID (toggle) |

### Settings Schema

```json
{
  "speech": {
    "rate": -25,
    "pitch": -25,
    "volume": 75,
    "voice_id": "piper:/usr/share/piper-voices/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx",
    "backend": "piper",
    "output_module": "rhvoice",
    "kokoro": {
      "speed": 1.0,
      "voice_blend": "",
      "blend_ratio": 0.5,
      "emotion_preset": "neutral",
      "lang_code": "p"
    }
  },
  "text": {
    "expand_abbreviations": true,
    "process_urls": false,
    "process_special_chars": true,
    "strip_formatting": true,
    "max_chars": 0
  },
  "shortcut": {
    "keybinding": "<Alt>v",
    "enabled": true,
    "show_in_launcher": true
  },
  "window": {
    "width": 560,
    "height": 680,
    "maximized": false
  },
  "history": {
    "enabled": false,
    "save_audio": true,
    "save_text": true,
    "playback_mode": "interrupt"
  },
  "show_welcome": true
}
```

### Legacy Migration

The app automatically detects old-format settings in `~/.config/tts-biglinux/` (individual files: `rate`, `pitch`, `volume`, `voice`) and migrates them to the unified JSON format.

---

## Internationalization

### i18n System

Translation uses gettext `.po` files with a custom Python parser (not binary `.mo`):

1. **Locale detection**: `LANGUAGE` → `LC_ALL` → `LC_MESSAGES` → `LANG`
2. **File lookup**: tries `pt-BR` and `pt_BR` variants, then base code `pt`
3. **Search paths**: `./locale/` (dev) → `/usr/share/tts-biglinux/locale/` (installed)

```python
from utils.i18n import _
label.set_text(_("Ready to speak"))  # → "Pronto para falar" in pt-BR
```

212 translatable strings across all source files.

### Available Languages (29)

| Code | Language | Code | Language |
|------|----------|------|----------|
| bg | Bulgarian | ko | Korean |
| ca | Catalan | nl | Dutch |
| cs | Czech | no | Norwegian |
| da | Danish | pl | Polish |
| de | German | pt | Portuguese |
| el | Greek | pt-BR | Portuguese (Brazil) |
| en | English | ro | Romanian |
| es | Spanish | ru | Russian |
| et | Estonian | sk | Slovak |
| fi | Finnish | sv | Swedish |
| fr | French | tr | Turkish |
| he | Hebrew | uk | Ukrainian |
| hr | Croatian | zh | Chinese |
| hu | Hungarian | is | Icelandic |
| it | Italian | ja | Japanese |

### Adding a New Translation

1. Copy the template: `cp locale/tts-biglinux.pot locale/<code>.po`
2. Translate the `msgstr` entries in the `.po` file
3. The app loads `.po` files directly — no compilation step needed

---

## Technical Details

### Rust Native Engine

The `tts-engine` crate provides zero-overhead TTS backends via PyO3:

- **espeak-ng FFI**: `unsafe extern "C"` bindings to `libespeak-ng.so`. `OnceLock` for thread-safe one-time initialization. No subprocess, no IPC — direct function calls
- **Piper ONNX**: `ort` 2.0 for inference, `hound` for WAV encoding, `rodio` for playback. Model sessions cached in `Mutex<Option<CachedModel>>` — loaded once, reused across calls
- **Audio**: `rodio` with `AtomicBool` stop flag for interruptible playback. Dedicated audio thread (OutputStream is `!Send + !Sync`)
- **Error handling**: `thiserror` derive macro, proper `Result` propagation to Python via `PyRuntimeError`

Build: `ORT_LIB_LOCATION=/usr/lib ORT_PREFER_DYNAMIC_LINK=1 cargo build --release`

Clippy: 0 quality warnings (`clippy::all` + `clippy::pedantic` + `clippy::nursery`). Only expected `unsafe_code` warnings from FFI.

### Text Processing Pipeline

`text_processor.py` applies transformations before synthesis:

1. **Strip formatting**: removes HTML tags, Markdown bold/italic/code, headers, lists, links
2. **URL handling**: removes or keeps `https?://\S+`
3. **Abbreviation expansion** (language-aware): ~65 Portuguese, ~30 English, ~10 Spanish
4. **Special characters** (language-aware): `#` → "hash"/"cerquilha", `@` → "at"/"arroba"
5. **Cleanup**: collapse multiple spaces/newlines

### Clipboard Access

`clipboard_service.py` auto-detects the display server:

- **Wayland**: `wl-paste --primary --no-newline`, fallback to regular clipboard
- **X11**: `xsel --primary -o`, fallback to `xsel -o`, then `xclip`
- Detection: `XDG_SESSION_TYPE == "wayland"` or `WAYLAND_DISPLAY` set

### System Tray IPC Protocol

JSON lines over stdin/stdout between GTK parent and PySide6 child:

```
GTK (parent)                    Qt (child)
    │                                │
    │── {"cmd":"set_menu",...} ──────▶│  configure context menu
    │── {"cmd":"set_tooltip",...} ───▶│  set tooltip
    │── {"cmd":"set_speaking",...} ──▶│  update speaking state
    │                                │
    │◀── {"event":"ready"} ─────────│  tray icon visible
    │◀── {"event":"activate"} ──────│  left click
    │◀── {"event":"menu","id":1} ───│  menu item clicked
    │                                │
    │── {"cmd":"quit"} ─────────────▶│  terminate
```

### Async and Threading

- **Debouncer**: `GLib.timeout_add(500ms)` — saves settings after 500ms of inactivity
- **run_in_thread**: heavy ops (clipboard, voice discovery) in daemon threads, results via `GLib.idle_add()`
- **TTS monitoring**: 300ms polling via `GLib.timeout_add()` to detect speech completion
- **UI thread**: no blocking operations on GTK main thread

---

## Building from Source

### PKGBUILD

```bash
cd pkgbuild && makepkg -si
```

The build process:
1. Compiles the Rust `tts-engine` crate with `cargo build --release`
2. Copies the `usr/` tree (Python code, icons, desktop file, locale)
3. Installs `libtts_engine.so` as `tts_engine.so` into the application directory
4. Sets executable permissions on `usr/bin/*`

### Package Versioning

```
pkgver=$(date +%y.%m.%d)    # Date-based: e.g. 26.06.19
pkgrel=$(date +%H%M)        # Release by hour (multiple builds/day)
arch=('x86_64')              # x86_64 only (native Rust binary)
```

### Project Structure

```
tts-biglinux/
├── locale/                          # Translation source files (.po, .pot)
│   ├── tts-biglinux.pot             # Template (212 strings)
│   ├── pt-BR.po                     # Brazilian Portuguese (100%)
│   └── ...                          # 28 more languages
├── pkgbuild/
│   └── PKGBUILD                     # Arch/BigLinux packaging
├── tts-engine/                      # Native Rust TTS engine
│   ├── Cargo.toml                   # Dependencies and lints
│   ├── build.rs                     # Link: pyo3 + libespeak-ng
│   └── src/
│       ├── lib.rs                   # PyO3 module entry
│       ├── audio.rs                 # rodio playback + stop
│       ├── error.rs                 # TtsError enum
│       └── backends/
│           ├── espeak.rs            # espeak-ng FFI
│           └── piper.rs             # ONNX inference + phonemization
├── usr/
│   ├── bin/
│   │   ├── biglinux-tts             # Entry: cd + exec python main.py
│   │   └── biglinux-tts-speak       # Standalone toggle script (Alt+V)
│   └── share/
│       ├── applications/
│       │   └── br.com.biglinux.tts.desktop
│       ├── biglinux/tts-biglinux/   # Python application code
│       │   ├── main.py              # CLI args, logging, App.run()
│       │   ├── application.py       # Adw.Application lifecycle
│       │   ├── config.py            # Constants, enums, dataclasses
│       │   ├── window.py            # Adw.ApplicationWindow
│       │   ├── services/            # TTS, voice mgr, clipboard, tray
│       │   ├── ui/                  # Views, dialogs, components
│       │   ├── utils/               # i18n, async, speechd
│       │   └── resources/           # CSS, __init__.py
│       ├── icons/hicolor/scalable/  # SVG icons (app + status)
│       └── khotkeys/                # KDE Plasma 5 shortcut
└── README.md
```

---

## License

Licensed under [GPL-3.0-or-later](https://www.gnu.org/licenses/gpl-3.0.html).

TTS engines (speech-dispatcher, espeak-ng, RHVoice, Piper, Kokoro) have their own licenses. See their respective documentation.

---

## Authors

- **Tales A. Mendonça** — BigLinux project creator
- **Bruno Gonçalves Araujo** — BigLinux project, initial implementation
- **Rafael Ruscher** — Architecture, GTK4 rewrite, Rust engine, v3.0–4.0

---

<p align="center">
  <img src="usr/share/icons/hicolor/scalable/apps/biglinux-tts.svg" alt="BigLinux TTS" width="48">
  <br>
  <em>BigLinux TTS v4.0.0 — Text-to-speech for Linux desktop</em>
</p>
