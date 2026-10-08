# BigLinux TTS

<p align="center">
  <img src="usr/share/icons/hicolor/scalable/apps/biglinux-tts.svg" alt="BigLinux TTS" width="128">
</p>

<p align="center">
  <strong>Select any text, press Alt+V, and hear it — native text-to-speech for the Linux desktop</strong>
</p>

<p align="center">
  <a href="https://www.gnu.org/licenses/gpl-3.0.html"><img src="https://img.shields.io/badge/license-GPL--3.0-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/version-4.0.0-brightgreen.svg" alt="Version">
  <img src="https://img.shields.io/badge/GTK-4-green.svg" alt="GTK4">
  <img src="https://img.shields.io/badge/libadwaita-1.5+-purple.svg" alt="libadwaita 1.5+">
  <img src="https://img.shields.io/badge/Rust-1.85+-orange.svg" alt="Rust">
  <img src="https://img.shields.io/badge/Python-3.10+-yellow.svg" alt="Python">
  <img src="https://img.shields.io/badge/engines-4-red.svg" alt="4 TTS engines">
  <img src="https://img.shields.io/badge/languages-30-lightgrey.svg" alt="30 languages">
  <img src="https://img.shields.io/badge/tests-100-success.svg" alt="100 tests">
</p>

---

## Table of Contents

- [About](#about)
- [Screenshots](#screenshots)
- [History](#history)
- [Features](#features)
- [TTS Engines](#tts-engines)
- [Architecture](#architecture)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Internationalization](#internationalization)
- [Technical Details](#technical-details)
- [Testing](#testing)
- [Building from Source](#building-from-source)
- [License](#license)
- [Authors](#authors)

---

## About

**BigLinux TTS** is a native Linux desktop application that reads text aloud. Built with GTK4, libadwaita and a native Rust engine, it is the built-in text-to-speech tool of [BigLinux](https://www.biglinux.com.br/) — a Brazilian Linux distribution based on Manjaro/Arch Linux.

Select any text on screen, press **Alt+V**, and hear it read aloud. Press again to stop. No complicated setup: installing the package brings a ready Brazilian Portuguese voice.

### Use Cases

- **Accessibility** — screen reading for users with visual impairments or reading difficulties
- **Multitasking** — listen to articles, documents, and emails while doing other things
- **Language learning** — hear correct pronunciation in 100+ languages
- **Proofreading** — catch writing errors by listening to what was written
- **Productivity** — convert passive reading into active listening

### What Sets It Apart

1. **4 TTS engines** — RHVoice, espeak-ng (native FFI), Piper (native ONNX) and Kokoro neural voices
2. **Native Rust engine** — espeak-ng via direct FFI and Piper ONNX inference via `ort`; the GIL is released during synthesis, so the window never freezes
3. **Honest feedback** — the window shows the real state (loading the voice, speaking, stopped, error with the reason and how to fix it); nothing fails silently
4. **Reliable global shortcut** — registered through the desktop's own API and confirmed before the app says it works; conflicts are named
5. **Smart text processing** — expands abbreviations, normalizes numbers, pronounces special characters, strips HTML/Markdown
6. **Desktop integration** — system tray with playback controls, MPRIS media controls, notifications, launcher pinning
7. **Modern UI** — GTK4 + libadwaita (GNOME HIG), adaptive down to 360 px wide, light/dark, RTL
8. **30 languages** — gettext `.po` catalogs (Portuguese of Brazil and of Portugal kept separate)

---

## Screenshots

| Ready to speak (dark) | Ready to speak (light) |
|---|---|
| ![Main window, dark theme](screenshots/main-dark.png) | ![Main window, light theme](screenshots/main-light.png) |

| Speaking | Error with a way out |
|---|---|
| ![Speaking state](screenshots/speaking.png) | ![Error state with Open Voice Manager](screenshots/error.png) |

| Engine status | Voice Manager |
|---|---|
| ![Engine status for each engine](screenshots/engine-status.png) | ![Voice Manager with Kokoro voices](screenshots/voice-manager.png) |

| History (main menu → History) | Advanced options |
|---|---|
| ![History view](screenshots/history.png) | ![Advanced options with the shortcut](screenshots/advanced-options.png) |

| Narrow window (360–600 px) | First run | Brazilian Portuguese |
|---|---|---|
| ![Narrow layout](screenshots/narrow.png) | ![Welcome dialog](screenshots/welcome.png) | ![Main window in pt-BR](screenshots/main-pt-BR.png) |

<sub>Screenshots of the real application (KDE Plasma 6 fonts/theme, rendered headless in a clean profile; sample history entries).</sub>

---

## History

BigLinux TTS was born from a practical need: making text-to-speech accessible and easy on the Linux desktop.

| Date | Version | Milestone |
|------|---------|-----------|
| Sep 2021 | — | First commit by Bruno Gonçalves: initial web-based interface |
| Mar 2022 | — | Rafael Ruscher joins: icon design, CSS refinements, translations |
| Aug 2022 | — | PKGBUILD packaging, i18n with 29 locales, CI/CD workflow |
| Dec 2023 | — | Volume/pitch/rate range inputs, UI polish |
| Feb 2026 | 3.0 | **Full rewrite**: web UI → GTK4 + libadwaita + Python. speech-dispatcher integration, Piper Neural TTS, tray icon (PySide6 subprocess), text processor with abbreviation expansion |
| Mar 2026 | 3.1 | Native RHVoice backend, parallel voice discovery, Python DBus launcher |
| Mar 2026 | 3.2 | Voice Manager dialog with install/remove, theme support, khotkeys sync |
| Jun 2026 | **4.0** | **Native Rust engine** (PyO3): espeak-ng FFI, Piper ONNX inference via `ort` with model caching (7× faster short text). Kokoro Neural TTS integration. Complete i18n audit. Full codebase cleanup |
| Sep 2026 | 4.0 | History in SQLite with retention, Piper streaming, systray playback controls (pause/resume), MPRIS mini-player, pt-BR number/date normalization, diagnostics |
| Oct 2026 | 4.0 | Kokoro fixed for the global shortcut, silent startup, real loading/error states, *Ready to speak* card, engine status, KGlobalAccel shortcut registration with conflict detection, GIL released in Rust, adaptive layout, tray icon breathing on HiDPI, 100 automated tests |

---

## Features

### Text Reading

- **Configurable global hotkey** (default Alt+V) — select text anywhere, press to speak, press again to stop (also while the voice is still loading)
- **"Ready to speak" card** — shows the real state (ready, loading voice, speaking, stopped, error with the reason and a fix such as *Open Voice Manager*) and the shortcut as keyboard keys, with the desktop's confirmation that it is active
- **Engine status** — RHVoice, espeak-ng, Piper and Kokoro each show *Not installed*, *No voices*, *Ready*, and for the selected engine *Loading*, *Speaking* or *Error*
- **System tray icon** — breathes (opacity only) while reading; left-click speaks or stops, right-click offers Pause/Play and Stop while reading, plus Read text, Settings and Quit; the tooltip shows the current shortcut
- **Notifications** — what is being read, and a clear message when something fails or nothing is selected
- **Built-in voice test** — text field to type and hear with the current voice settings
- **Optional history** — keep and replay previous readings (main menu → History, Ctrl+H)
- **Silent start** — opening the app never speaks and never starts speech-dispatcher

### Voice Control

- **Speed** — scale from -100 (slow) to +100 (fast)
- **Pitch** — scale from -100 (low) to +100 (high); for Piper it controls *expressiveness*
- **Volume** — scale from 0 (mute) to 100 (max)
- **Voice selection** — list filtered by engine: "Name — Language · Neural / High quality"
- **Kokoro** — expression presets (neutral, happy, calm, urgent, narrative) and blending with a second voice

### Voice Manager

- Install and remove voices for RHVoice, Piper, espeak-ng (pacman packages via `pkexec`) and Kokoro (downloaded voice files)
- Play/stop a sample with the same button, search by name or language, size shown before installing
- Kokoro downloads: real progress, **Cancel**, verification (incomplete or damaged files are rejected), free-space check, atomic update of `voices.bin`
- Removal asks for confirmation

### Text Processing

| Feature | Description | Example |
|---------|-------------|---------|
| Expand abbreviations | Converts slang/abbreviations per language | `tb` → "também", `btw` → "by the way" |
| Normalize numbers | Reads numbers, dates and times naturally (pt-BR) | `1.234,56` → "mil duzentos e trinta e quatro vírgula cinquenta e seis" |
| Special characters | Pronounces symbols by name | `#` → "hash", `@` → "at" |
| Strip formatting | Removes HTML tags, Markdown bold/italic/code | `**bold**` → "bold" |
| URL handling | Option to read or skip links | `https://...` → read or skip |
| Character limit | Truncates long text | Unlimited, 1K, 5K, 10K, 50K, 100K |

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Alt+V (default) | Speak/stop selected text (toggle) |
| Ctrl+H | Open History |
| Escape / Alt+Left | Back from History |
| F10 | Open the main menu |
| Ctrl+Q | Quit application |

Changing the shortcut (Advanced options → Keyboard shortcut) registers it with the desktop and only reports success once the desktop confirms it. On KDE Plasma this uses KGlobalAccel over D-Bus: a key already used by another action is reported by name and the previous shortcut is kept. A shortcut needs Ctrl, Alt or Super (or a function key) so normal typing is never captured. GNOME, XFCE and Cinnamon use their own settings.

---

## TTS Engines

### 1. RHVoice

High-quality multilingual TTS — **the default engine**, installed with the package together with the Brazilian Portuguese voice *Letícia* and the BigLinux pronunciation dictionary. Played as `RHVoice-test | aplay` (no speech-dispatcher), both for reading and for the Voice Manager preview.

| Voice | Language | Quality |
|-------|----------|---------|
| Letícia F123 | pt-BR | ★★★★ |
| Evgeniy | English | ★★★★ |
| + others | Multiple | ★★★–★★★★ |

A legacy speech-dispatcher backend (SSIP) remains only for migrated old settings; the app talks to speech-dispatcher only when that backend is actually used.

### 2. espeak-ng (Native FFI) ⚡

Direct C FFI to `libespeak-ng.so` from the Rust engine — no subprocess for synthesis.

- `AUDIO_OUTPUT_SYNCHRONOUS` mode: espeak-ng renders PCM and never opens the audio device itself (it is also Piper's phonemizer)
- One-time initialization via `OnceLock`, all calls serialized
- 100+ languages with basic quality

### 3. Piper (Native ONNX Inference) ★★★★★

Neural TTS with near-human quality. ONNX models run locally through the `ort` crate — the `piper-tts` binary is only a fallback.

**Pipeline**: text → espeak-ng IPA phonemes (FFI) → phoneme IDs → ONNX model → WAV → `aplay`

| Feature | Detail |
|---------|--------|
| Runtime | `ort` 2.0 (system ONNX Runtime, dynamic link) |
| Model cache | load once, reuse; prewarmed in the background after startup |
| Long text | sentence chunks synthesized while the previous one plays |
| Performance | 7× faster than the subprocess for short text |

### 4. Kokoro (Neural TTS) ★★★★★

Neural voices in Portuguese, English, Spanish, French, Italian, Japanese, Hindi and Chinese, with voice blending and expression presets. On BigLinux it runs through the `koko` binary from `biglinux-kokoro-tts` (model + base voices); the Python `kokoro` package (PyTorch) is used instead when installed.

- One command builder (`kokoro_voice_service.build_koko_command`) serves both reading and the Voice Manager preview: explicit model, voices file, language from the voice prefix, and a private writable work directory (`$XDG_RUNTIME_DIR/biglinux-tts/koko`)
- `koko pipe` streams sentence by sentence; the card shows *Loading voice* until koko reports that audio started
- The selected voice is checked against `voices.bin` before speaking (koko would silently use another voice)
- Voice blending: mix two voices (`voice.W+voice.W`); expression presets adjust the speed
- More voices can be downloaded in the Voice Manager

### Automatic Voice Discovery

All engines are scanned in parallel in a background thread:

1. **RHVoice**: scans `/usr/share/RHVoice/voices/` (speech-dispatcher is not queried: starting it can make other installed modules speak)
2. **espeak-ng**: `espeak-ng --voices` → language code, gender
3. **Piper**: scans `/usr/share/piper-voices/` and `~/.local/share/piper-voices/` for `.onnx` + `.onnx.json`
4. **Kokoro**: `koko voices` on the active `voices.bin` (system base voices + user downloads)

Result: a `VoiceCatalog` with every voice plus, per engine, whether it is installed and has voices (shown as *Engine status*).

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│ main.py — CLI args (--speak, --debug, --version), logging, App.run()      │
├──────────────────────────────────────────────────────────────────────────┤
│ application.py — TTSApplication (Adw.Application, single instance)        │
│   startup → shortcut registration (worker) → tray → MPRIS → prewarm       │
│   --speak: capture selection (worker) → speak / stop (toggle)             │
├────────────────────────┬────────────────────────┬────────────────────────┤
│ UI layer (GTK thread)  │ Service layer          │ Data layer             │
├────────────────────────┼────────────────────────┼────────────────────────┤
│ window.py              │ tts_service.py         │ config.py              │
│  ├ OverlaySplitView    │  ├ speak / stop        │  ├ AppSettings         │
│  ├ header + main menu  │  ├ LOADING/SPEAKING/   │  ├ TTSBackend/TTSState │
│  └ status bar          │  │  ERROR + reasons    │  └ JSON load/save      │
│ main_view.py           │  └ per-engine paths    │ settings_service.py    │
│  ├ Ready to speak card │ shortcut_service.py    │  └ debounced save      │
│  ├ engine status       │  └ KGlobalAccel D-Bus  │ history_db.py          │
│  └ voice settings      │ voice_manager.py       │  └ SQLite + retention  │
│ components.py          │  └ discovery + status  │                        │
│  └ ShortcutKeys, pills │ kokoro_voice_service.py│                        │
│ voice_manager_dialog.py│  └ koko argv, downloads│                        │
│ history_view.py        │ clipboard_service.py   │                        │
│ audio_player.py (Gst)  │ tray_service.py ──────────▶ PySide6 subprocess │
│ welcome_dialog.py      │  tray_icon_frames.py   │   (QSystemTrayIcon)    │
│                        │ mpris_service.py       │                        │
│                        │ desktop_integration_*  │                        │
├────────────────────────┴────────────────────────┴────────────────────────┤
│ tts_engine.so (Rust / PyO3) — GIL released during synthesis               │
│   espeak-ng FFI (synchronous) · Piper ONNX via ort (model cache)          │
├──────────────────────────────────────────────────────────────────────────┤
│ External: RHVoice-test · koko · aplay · wl-paste/xsel · pkexec pacman     │
└──────────────────────────────────────────────────────────────────────────┘
```

### Request Lifecycle (TTS State Machine)

```
              speak()                       audio starts
   IDLE ─────────────────▶ LOADING ─────────────────────▶ SPEAKING
    ▲                        │  (synthesizing,              │
    │                        │   loading the model)         │ playback ends
    │       stop() / Alt+V   │                              │
    ├────────────────────────┴──────────────────────────────┤
    │                                                        │
    │                       engine failed                    │
    │            ┌──────────────── ERROR ◀───────────────────┘
    └────────────┘  (reason + recovery action kept until the next speak/stop)
```

- `LOADING` is real: engines that synthesize first (Piper, Kokoro, espeak-ng) switch to `SPEAKING` when the player starts, or when `koko` reports *Streaming audio*
- `ERROR` carries a translated message and an action (`voice-manager` or `retry`); it is never reset to idle behind the user's back
- A monotonic request generation discards stale audio from an interrupted request
- State listeners always run on the GTK main thread; stale notifications from workers are dropped

---

## Installation

### BigLinux / Manjaro / Arch Linux

```bash
sudo pacman -S tts-biglinux
```

This installs the application, the native engine, RHVoice with the Brazilian Portuguese voice *Letícia*, espeak-ng, the tray icon (PySide6), the History player (GStreamer) and the clipboard tools — everything needed to select text and press Alt+V.

Optional voices:

```bash
# Kokoro neural voices (BigLinux community package)
sudo pacman -S biglinux-kokoro-tts

# Piper neural voices (biglinux-testing)
sudo pacman -S piper-voices-pt-BR

# English RHVoice voice
sudo pacman -S rhvoice-voice-evgeniy-eng
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

# Build the native Rust engine against the SYSTEM onnxruntime
cd tts-engine
ORT_LIB_LOCATION=/usr/lib ORT_PREFER_DYNAMIC_LINK=1 cargo build --release
cd ..

# Link the .so into the application directory
ln -sf ../../../../tts-engine/target/release/libtts_engine.so \
  usr/share/biglinux/tts-biglinux/tts_engine.so

# Run
cd usr/share/biglinux/tts-biglinux
python main.py --debug
```

### Dependencies

#### Required (installed with the package)

| Package | Why |
|---------|-----|
| `python`, `python-gobject` | Python 3 and PyGObject |
| `gtk4`, `libadwaita>=1:1.5` | Interface (AlertDialog, OverlaySplitView, Breakpoint) |
| `python-numpy` | Kokoro voice download conversion (`.pt` → `.npy`) |
| `polkit` | `pkexec` to install/remove voice packages |
| `onnxruntime` | Piper neural inference (system library, dynamic link) |
| `espeak-ng` | espeak voices and Piper phonemizer (linked by `tts_engine.so`) |
| `alsa-utils` | `aplay` — every engine plays through it |
| `sox` | Volume control on the subprocess fallback paths |
| `gstreamer`, `gst-plugins-base`, `gst-plugins-good` | History player (`playbin`, `wavparse`, audio sink) |
| `rhvoice`, `rhvoice-voice-leticia-f123`, `rhvoice-brazilian-portuguese-complementary-dict-biglinux` | Default engine and voice, ready out of the box |
| `speech-dispatcher` | Python `speechd` module; legacy speech-dispatcher voices |
| `wl-clipboard-rs`, `xsel` | Selected text on Wayland (`wl-paste`) and X11 |
| `pyside6`, `qt6-svg` | System tray icon (SVG icons) |

#### Build and check

| Package | Why |
|---------|-----|
| `git`, `rust>=1.85`, `cargo` | Fetch sources, build `tts_engine.so` |
| `python-pytest` | `check()` runs the test suite |

#### Optional

| Package | Why |
|---------|-----|
| `biglinux-kokoro-tts` | Kokoro neural voices (`koko`, model, base voices) — BigLinux community package |
| `piper-voices-pt-BR` | Brazilian Portuguese Piper voices |
| `piper-tts-bin` | Piper command-line fallback (the native engine needs only voices) |
| `rhvoice-voice-evgeniy-eng` | RHVoice English voice |
| `xclip` | X11 selection capture when `xsel` is missing |
| `python-kokoro`, `python-pytorch`, `python-soundfile` | Alternative PyTorch-based Kokoro engine |

---

## Usage

### GUI

```bash
biglinux-tts            # Open the window (or bring the running one forward)
biglinux-tts --debug    # Detailed logging
biglinux-tts --version  # Print the version
```

### Keyboard Shortcut (CLI)

```bash
biglinux-tts-speak      # What Alt+V runs: biglinux-tts --speak
```

`--speak` is forwarded to the running instance (GApplication) or starts one:

1. Reading or loading a voice → stop (in the default *interrupt* playback mode)
2. Text selected → read it with the configured engine and voice
3. Nothing selected → a short notification says so

### Typical Workflow

1. **First launch**: the welcome dialog explains features and setup
2. **Configure**: choose the engine and voice, adjust speed/pitch/volume
3. **Test**: type text in the test field and press *Test voice* (or Enter)
4. **Daily use**: select text anywhere → Alt+V → listen

---

## Configuration

### File Locations

| Path | Content |
|------|---------|
| `~/.config/biglinux-tts/settings.json` | All app settings (JSON) |
| `~/Music/tts-biglinux/history.db` | Reading history (SQLite) and saved audio, when enabled |
| `~/.local/share/biglinux-tts/kokoro-voices/voices.bin` | Kokoro voices downloaded by the user (system base voices + downloads) |
| `$XDG_RUNTIME_DIR/biglinux-tts/koko/` | Kokoro scratch audio (private, temporary) |
| `~/.local/share/applications/biglinux-tts-speak.desktop` | Launcher bound to the global shortcut |
| `/usr/share/tts-biglinux/locale/*.po` | Installed translations |

### Settings Schema (defaults)

```json
{
  "speech": {
    "rate": -25,
    "pitch": -25,
    "volume": 75,
    "voice_id": "",
    "backend": "rhvoice",
    "output_module": "",
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
    "normalize_numbers": true,
    "max_chars": 0
  },
  "shortcut": {
    "keybinding": "<Alt>v",
    "enabled": true,
    "show_in_launcher": true
  },
  "window": {
    "width": 900,
    "height": 740,
    "maximized": false,
    "tray_warning_shown": false
  },
  "history": {
    "enabled": false,
    "save_audio": true,
    "save_text": true,
    "playback_mode": "interrupt",
    "max_entries": 1000,
    "max_age_days": 0
  },
  "show_welcome": true,
  "show_media_player": true,
  "config_version": 1
}
```

`playback_mode`: `interrupt` (a new reading replaces the current one; the shortcut toggles), `queue` or `simultaneous`.

### Legacy Migration

Old-format settings in `~/.config/tts-biglinux/` (individual files: `rate`, `pitch`, `volume`, `voice`) are detected and migrated to the JSON format automatically.

---

## Internationalization

### i18n System

Translations are gettext `.po` files read by a small Python parser (no `.mo` compilation):

1. **Locale detection**: `LANGUAGE` → `LC_ALL` → `LC_MESSAGES` → `LANG`
2. **File lookup**: `pt-BR` / `pt_BR` variants, then the base code `pt`
3. **Search paths**: `<repo>/locale/` (development) → `/usr/share/tts-biglinux/locale/` (installed)

```python
from utils.i18n import _
label.set_text(_("Ready to speak"))  # → "Pronto para falar" in pt-BR
```

300 translatable strings; `locale/tts-biglinux.pot` is the template. The CI workflow translates new strings for the other languages; Portuguese (Brazil) and Portuguese (Portugal) are maintained as separate catalogs.

### Available Languages (30)

| Code | Language | Code | Language |
|------|----------|------|----------|
| bg | Bulgarian | ja | Japanese |
| ca | Catalan | ko | Korean |
| cs | Czech | nl | Dutch |
| da | Danish | no | Norwegian |
| de | German | pl | Polish |
| el | Greek | pt | Portuguese (Portugal) |
| en | English | pt-BR | Portuguese (Brazil) |
| es | Spanish | ro | Romanian |
| et | Estonian | ru | Russian |
| fi | Finnish | sk | Slovak |
| fr | French | sv | Swedish |
| he | Hebrew | tr | Turkish |
| hr | Croatian | uk | Ukrainian |
| hu | Hungarian | zh | Chinese |
| is | Icelandic | it | Italian |

### Adding a New Translation

1. Copy the template: `cp locale/tts-biglinux.pot locale/<code>.po`
2. Translate the `msgstr` entries (keep `{placeholders}` unchanged)
3. Validate: `msgfmt --check --check-format -o /dev/null locale/<code>.po`
4. The app loads `.po` files directly — no compilation step

---

## Technical Details

### Rust Native Engine (`tts-engine/`)

```
tts-engine/
├── Cargo.toml          # PyO3 0.25, ort 2.0, rodio 0.20, hound, serde, thiserror
├── build.rs            # Link args: pyo3 + libespeak-ng
└── src/
    ├── lib.rs          # PyO3 module: version, speak_espeak, synthesize_espeak,
    │                   #   speak_piper, synthesize_piper, load_piper, stop
    ├── audio.rs        # rodio playback with AtomicBool stop flag
    ├── error.rs        # TtsError enum (thiserror)
    └── backends/
        ├── espeak.rs   # FFI to libespeak-ng (synchronous mode, serialized)
        └── piper.rs    # ONNX pipeline: phonemize → IDs → infer → WAV
```

- Every synthesis and model load runs inside `py.allow_threads`: the GTK main loop keeps running. Measured on a 500-character Piper synthesis: the main loop froze 1788 ms before, 10 ms after
- The application plays the returned WAV with `aplay`, so playback can be paused (SIGSTOP/SIGCONT) and stopped like every other engine
- Build against the system ONNX Runtime: `ORT_LIB_LOCATION=/usr/lib ORT_PREFER_DYNAMIC_LINK=1 cargo build --release --locked` (linking the pip `onnxruntime` breaks the module at runtime)

### Text Processing Pipeline

`text_processor.py` applies transformations before synthesis:

1. **Strip formatting**: HTML tags, Markdown bold/italic/code, headers, lists, links
2. **URL handling**: remove or keep `https?://\S+`
3. **Abbreviation expansion** (language-aware): ~65 Portuguese, ~30 English, ~10 Spanish
4. **Number normalization** (pt-BR): numbers, decimals, dates and times
5. **Special characters** (language-aware): `#` → "hash"/"cerquilha", `@` → "at"/"arroba"
6. **Cleanup**: collapse spaces/newlines; long text is split into sentence chunks

### Selected Text Capture

`clipboard_service.py` detects the display server:

- **Wayland**: `wl-paste --primary --no-newline --type text`, fallback to the regular clipboard
- **X11**: `xsel --primary -o`, fallback to `xsel -o`, then `xclip`
- Only text is requested (an image in the clipboard is skipped) and output is decoded leniently

### Global Shortcut (`shortcut_service.py`)

- One parser/formatter/validator for the GTK accelerator (`<Alt>v` → keys *Alt + V*, Qt key code for KDE)
- **KDE Plasma**: KGlobalAccel D-Bus — `doRegister` + `setForeignShortcut` for `biglinux-tts-speak.desktop/_launch`, then `allShortcutInfos` read-back; `getGlobalShortcutsByKey` finds other actions on the key. Stale components from old versions are cleaned up
- **GNOME / XFCE / Cinnamon**: their keybinding settings, with `biglinux-tts-speak` as the command
- Registration runs in a worker; the card, Advanced options, status bar and tray tooltip update when the desktop answers

### speech-dispatcher Policy

The app never starts speech-dispatcher on its own: no query at startup, no cancel on Stop unless the legacy speech-dispatcher backend was used. Starting the daemon runs every installed output module, and some of them speak when started.

### System Tray IPC Protocol

JSON lines over stdin/stdout between the GTK app and the PySide6 helper:

```
GTK (parent)                         Qt (child)
    │── {"cmd":"set_menu", items} ────▶│  fixed rows, only properties change
    │── {"cmd":"set_tooltip"} ────────▶│  idle tooltip (shows the shortcut)
    │── {"cmd":"set_speaking"} ───────▶│  breathe / dim (paused) / steady
    │◀── {"event":"ready"} ────────────│
    │◀── {"event":"activate"} ─────────│  left click while idle → read
    │◀── {"event":"player","action":"stop"}  left click while reading
    │◀── {"event":"menu","id":N} ──────│  menu row clicked
    │── {"cmd":"quit"} ───────────────▶│
```

The breathing icon (`tray_icon_frames.py`) changes opacity only: every frame keeps the original pixel size **and** device-pixel ratio, so the icon never shrinks on scaled screens. Frames are cached per opacity level, sizes are limited to 16–48 px per screen scale, and the timer stops when reading ends or pauses.

### Async and Threading

- **Workers**: clipboard capture, voice discovery, shortcut registration, synthesis and downloads run in daemon threads; results return through `GLib.idle_add()`
- **TTS watch**: 300 ms `GLib.timeout_add()` follows the player process and its exit status
- **Settings**: saved 500 ms after the last change
- **No blocking work on the GTK main thread**

---

## Testing

```bash
# Whole suite (GTK widget tests skip automatically without a display)
python -m pytest -q -p no:cacheprovider tests

# Headless GTK, e.g. in CI
gtk4-broadwayd :5 & GDK_BACKEND=broadway BROADWAY_DISPLAY=:5 python -m pytest -q tests

# Rust engine
cd tts-engine && ORT_LIB_LOCATION=/usr/lib ORT_PREFER_DYNAMIC_LINK=1 cargo test --release --locked

# Lint and translations
ruff check usr/share/biglinux/tts-biglinux tests
for f in locale/*.po; do msgfmt --check --check-format -o /dev/null "$f"; done
```

100 tests cover, among others:

| Area | Tests |
|------|-------|
| Request lifecycle | `test_tts_lifecycle.py` — a fake `koko` process (real subprocess): LOADING → SPEAKING → IDLE, sticky errors, stop while loading, no speech-dispatcher on stop, listeners on the main thread |
| Kokoro | `test_koko_workdir.py`, `test_kokoro_download.py` — private work dir, shared preview/playback command, blend argv |
| Interface | `test_ui_states.py` — card states, shortcut keys and conflicts, engine status, saved voice kept, sidebar |
| Tray icon | `test_tray_breathing.py` — constant size at 100/125/175/200 % scale, smooth bounded breathing, timer stops |
| Text | `test_text_numbers.py`, `test_chunking.py`, `test_preview_lang.py` |
| Storage | `test_history_db.py`, `test_history.py`, `test_config.py` |
| Native engine | `test_engine_native.py`, `test_piper_streaming.py` (skipped without the built engine or the pt-BR Piper model) |

---

## Building from Source

### PKGBUILD

```bash
cd pkgbuild && makepkg -si
```

The build:

1. `build()` compiles the Rust `tts-engine` with `cargo build --release --locked` against the system ONNX Runtime
2. `check()` runs `cargo test` and the Python test suite (GTK widget tests skip without a display)
3. `package()` copies the `usr/` tree (Python code, icons, desktop files), installs `libtts_engine.so` as `tts_engine.so` in the application directory and the `.po` catalogs in `/usr/share/tts-biglinux/locale/`

### Package Versioning

```
pkgver=$(date +%y.%m.%d)    # Date-based: e.g. 26.10.08
pkgrel=$(date +%H%M)        # Several builds per day
arch=('x86_64')             # Native Rust binary
```

### Project Structure

```
tts-biglinux/
├── locale/                          # Translations (.po) and template (.pot)
├── pkgbuild/PKGBUILD                # Arch/BigLinux packaging
├── screenshots/                     # README images
├── tests/                           # pytest suite (100 tests)
├── tts-engine/                      # Native Rust engine (PyO3)
├── usr/
│   ├── bin/
│   │   ├── biglinux-tts             # Launcher: exec python main.py
│   │   └── biglinux-tts-speak       # Global shortcut: biglinux-tts --speak
│   └── share/
│       ├── applications/            # br.com.biglinux.tts.desktop
│       ├── biglinux/tts-biglinux/   # Python application
│       │   ├── main.py              # CLI args, logging, App.run()
│       │   ├── application.py       # Adw.Application lifecycle, shortcut, tray, notifications
│       │   ├── config.py            # Constants, enums, dataclasses
│       │   ├── window.py            # Window, main menu, status bar, adaptive layout
│       │   ├── services/            # TTS, voices, Kokoro, shortcut, clipboard, tray, MPRIS, history
│       │   ├── ui/                  # Main view, Voice Manager, History, welcome, components
│       │   ├── utils/               # i18n, async helpers, speechd
│       │   └── resources/           # style.css
│       ├── icons/hicolor/scalable/  # SVG icons (app + tray status)
│       └── khotkeys/                # KDE Plasma 5 legacy shortcut
└── README.md
```

---

## License

Licensed under [GPL-3.0-or-later](https://www.gnu.org/licenses/gpl-3.0.html).

TTS engines (speech-dispatcher, espeak-ng, RHVoice, Piper, Kokoro) have their own licenses. See their respective documentation.

---

## Authors

- **Tales A. Mendonça** — BigLinux project creator
- **Bruno Gonçalves Araujo <bigbruno@gmail.com>** — BigLinux project, initial implementation
- **Rafael Ruscher <rruscher@gmail.com>** — Architecture, GTK4 rewrite, Rust engine, v3.0–4.0

---

<p align="center">
  <img src="usr/share/icons/hicolor/scalable/apps/biglinux-tts.svg" alt="BigLinux TTS" width="48">
  <br>
  <em>BigLinux TTS v4.0.0 — Text-to-speech for Linux desktop</em>
</p>
