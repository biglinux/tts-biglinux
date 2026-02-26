# BigLinux TTS

<p align="center">
  <img src="usr/share/icons/hicolor/scalable/apps/biglinux-tts.svg" alt="BigLinux TTS" width="128">
</p>

<p align="center">
  <strong>Complete text-to-speech solution with graphical configuration for BigLinux</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/GTK-4-green.svg" alt="GTK4">
  <img src="https://img.shields.io/badge/libadwaita-1.x-purple.svg" alt="libadwaita">
  <img src="https://img.shields.io/badge/Python-3.10+-yellow.svg" alt="Python">
  <img src="https://img.shields.io/badge/engines-3-orange.svg" alt="3 TTS engines">
  <img src="https://img.shields.io/badge/languages-29-lightgrey.svg" alt="29 languages">
</p>

---

## Overview

**BigLinux TTS** is a native GTK4/libadwaita application for [BigLinux](https://www.biglinux.com.br/) that provides a complete text-to-speech system with graphical configuration. It supports three TTS engines (speech-dispatcher, espeak-ng, Piper Neural TTS), automatic voice discovery, customizable keyboard shortcuts, and real-time speech parameter control.

Built with modern GNOME HIG principles, it integrates seamlessly into KDE Plasma and any desktop environment running GTK4.

## Features

- **Multiple TTS Engines** — Choose between speech-dispatcher (RHVoice, espeak), espeak-ng direct, or Piper neural TTS for high-quality offline synthesis.
- **Automatic Voice Discovery** — Scans installed TTS engines and voices automatically. Detects RHVoice voices, espeak-ng languages, and Piper neural models.
- **Real-Time Voice Settings** — Adjust speed, pitch, and volume with live preview. All parameters are engine-aware with proper conversion for each backend.
- **Test Any Voice** — Built-in test field where you can type any text and hear it immediately with the selected voice and settings.
- **Piper Neural TTS Integration** — Full support for Piper neural voice models with automatic detection of `.onnx` models in `/usr/share/piper-voices/`. One-click installation of Piper engine and voice packages.
- **Customizable Keyboard Shortcut** — Change the global shortcut (default: Alt+V) directly from the app with a visual key capture dialog. Automatically updates KDE global shortcuts.
- **Pin to Taskbar** — Optionally pin the speak action to the KDE Plasma taskbar for one-click text reading without keyboard shortcuts.
- **Read Selected Text** — Select any text anywhere on your desktop and press the shortcut to read it aloud. Press again to stop.
- **Text Processing** — Expand abbreviations, read special characters by name, strip HTML/Markdown formatting, handle URLs, and set character limits.
- **speech-dispatcher Python API** — Uses the native `speechd.SSIPClient` Python API for reliable speech-dispatcher communication, bypassing CLI limitations.
- **Wayland & X11 Support** — Full clipboard integration using `wl-clipboard-rs` (Wayland) and `xsel` (X11).
- **Internationalization** — Translated into 29 languages via gettext `.po` files.
- **Persistent Settings** — All preferences saved to `~/.config/biglinux-tts/settings.json` following XDG specification.
- **Modern UI** — Adwaita design with hero section, preferences groups, expander rows, toast notifications, and responsive layout.

## Screenshots

<!-- Add screenshots here -->
<!-- ![Main Window](docs/screenshot-main.png) -->
<!-- ![Voice Settings](docs/screenshot-voices.png) -->
<!-- ![Shortcut Dialog](docs/screenshot-shortcut.png) -->

## TTS Engines

### speech-dispatcher (Default)

The primary TTS backend. Routes speech through the speech-dispatcher daemon, which supports multiple output modules:

| Module | Description |
|---|---|
| **RHVoice** | High-quality multilingual voices (Letícia F123 for pt-BR, Evgeniy-Eng for English) |
| **espeak-ng** | Lightweight, supports 100+ languages |
| **espeak** | Legacy espeak engine |
| **pico** | SVOX Pico TTS |
| **festival** | University of Edinburgh's Festival system |

Communication uses the Python `speechd.SSIPClient` API directly for reliable text delivery and callback support.

### espeak-ng (Direct)

Bypasses speech-dispatcher and calls espeak-ng directly. Useful for:
- Lower latency
- Simpler configuration
- Systems without speech-dispatcher

Supports 100+ languages and variants with configurable voice, speed (80–450 WPM), pitch (0–99), and volume.

### Piper (Neural TTS)

Offline neural text-to-speech using ONNX models. Produces natural-sounding speech:

- **Binary**: `piper-tts` (package: `piper-tts-bin`)
- **Voices**: `.onnx` models in `/usr/share/piper-voices/`
- **Audio**: Raw PCM output piped through `aplay` (22050 Hz, 16-bit)
- **Volume**: Adjusted via `sox` pipeline when available
- **Parameters**:
  - `--length_scale` — Speed control (0.3 = fast, 1.0 = normal, 2.5 = slow)
  - `--noise_scale` — Voice expressiveness/intonation variation
  - `--noise_w` — Phoneme duration variation
  - `--sentence_silence` — Pause between sentences

Auto-install dialog offers to install `piper-tts-bin` and language-specific voice packages via `pkexec pacman`.

## Requirements

| Dependency | Package | Required |
|---|---|---|
| Python | `python` (3.10+) | Yes |
| PyGObject | `python-gobject` | Yes |
| GTK 4 | `gtk4` | Yes |
| libadwaita | `libadwaita` (1.x) | Yes |
| speech-dispatcher | `speech-dispatcher` | Yes |
| espeak-ng | `espeak-ng` | Yes |
| X11 clipboard | `xsel` | Yes |
| Wayland clipboard | `wl-clipboard-rs` | Yes |
| ALSA utilities | `alsa-utils` | Yes |
| RHVoice | `rhvoice` | Optional |
| Piper TTS | `piper-tts-bin` | Optional |
| SoX | `sox` | Optional |

## Installation

### BigLinux / Manjaro / Arch Linux

```bash
# From the official BigLinux repository
sudo pacman -S tts-biglinux

# Optional: install RHVoice Brazilian Portuguese voice
sudo pacman -S rhvoice rhvoice-voice-leticia-f123

# Optional: install Piper neural TTS
sudo pacman -S piper-tts-bin piper-voices-pt-BR
```

### From Source

```bash
git clone https://github.com/biglinux/tts-biglinux.git
cd tts-biglinux
PYTHONPATH=src python3 -m biglinux_tts
```

### Building the Package (makepkg)

```bash
cd pkgbuild
makepkg -si
```

## Project Structure

```
tts-biglinux/
├── src/
│   └── biglinux_tts/
│       ├── __init__.py              # Package initialization
│       ├── __main__.py              # Entry point (python3 -m biglinux_tts)
│       ├── main.py                  # CLI argument parsing, logging setup
│       ├── application.py           # Adw.Application lifecycle
│       ├── window.py                # Main window (Adw.ApplicationWindow)
│       ├── config.py                # Configuration dataclasses, constants, I/O
│       ├── services/
│       │   ├── tts_service.py       # TTS speak/stop state machine
│       │   ├── voice_manager.py     # Voice discovery across all engines
│       │   ├── text_processor.py    # Text normalization, abbreviation expansion
│       │   ├── clipboard_service.py # Clipboard access (Wayland + X11)
│       │   └── settings_service.py  # JSON settings persistence
│       ├── ui/
│       │   ├── main_view.py         # Main settings view (hero, controls, sections)
│       │   ├── components.py        # Reusable widget factories (9 factory functions)
│       │   └── style.css            # Custom CSS (hero section, animations)
│       ├── utils/
│       │   ├── async_utils.py       # Thread helpers, debouncer
│       │   └── i18n.py              # Gettext internationalization helper
│       └── resources/
│           ├── __init__.py          # CSS loader (load_css)
│           └── style.css            # Application stylesheet
├── usr/
│   ├── bin/
│   │   ├── biglinux-tts             # Launch settings GUI
│   │   └── biglinux-tts-speak       # Read selected text via shortcut
│   ├── share/
│   │   ├── applications/
│   │   │   ├── bigtts.desktop                    # KDE global shortcut (Alt+V, NoDisplay)
│   │   │   └── br.com.biglinux.tts.desktop       # Settings app launcher
│   │   ├── icons/hicolor/scalable/apps/
│   │   │   └── biglinux-tts.svg                  # Application icon
│   │   └── locale/                               # Compiled translations (.mo, .json)
│   │       ├── bg/LC_MESSAGES/
│   │       ├── cs/LC_MESSAGES/
│   │       ├── ...                               # 28 languages
│   │       └── zh/LC_MESSAGES/
├── locale/                                       # Translation source files (.po, .pot)
├── pkgbuild/
│   └── PKGBUILD                                  # Arch/BigLinux package build script
├── pyproject.toml                                # Python project metadata
└── README.md
```

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      main.py / __main__.py                   │
│                   BigLinuxTTSApp (Adw.Application)           │
├──────────────┬──────────────────────┬────────────────────────┤
│   UI Layer   │   Service Layer      │   Configuration        │
├──────────────┼──────────────────────┼────────────────────────┤
│ window.py    │ tts_service.py       │ config.py              │
│ main_view.py │ voice_manager.py     │ settings_service.py    │
│ components.py│ text_processor.py    │                        │
│ style.css    │ clipboard_service.py │                        │
└──────────────┴──────────────────────┴────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
      speech-dispatcher   espeak-ng       piper-tts
      (speechd.SSIPClient)  (direct)     (stdin → aplay)
```

- **UI Layer** — GTK4 + libadwaita widgets following GNOME HIG. Hero section with live status, preferences groups for voice settings, TTS engine selection, text processing, and advanced options. Toast notifications for feedback.
- **Service Layer** — TTS state machine with speak/stop/cleanup lifecycle. Background voice discovery across all engines. Text normalization pipeline. Clipboard access for both Wayland and X11.
- **Configuration** — Typed dataclasses for all settings. JSON persistence at `~/.config/biglinux-tts/settings.json`. Sensible defaults with reset capability.

## How It Works

### Reading Selected Text

1. Select text anywhere on your desktop (browser, editor, terminal, etc.).
2. Press **Alt+V** (or your custom shortcut).
3. The selected text is captured via clipboard and spoken aloud.
4. Press **Alt+V** again to stop.

### Using the Taskbar Button

1. Open **Advanced options → Show speak button in app launcher**.
2. Toggle the switch **ON**.
3. The TTS icon is pinned to the KDE Plasma taskbar automatically.
4. Click the icon to speak selected text — click again to stop.

### Configuring Voice Settings

1. Launch the settings app: `PYTHONPATH=src python3 -m biglinux_tts` (or `biglinux-tts` when installed).
2. Select a **TTS Engine** (speech-dispatcher, espeak-ng, or Piper).
3. Choose a **Voice** from the discovered voices dropdown.
4. Adjust **Speed**, **Pitch**, and **Volume** sliders.
5. Type text in the test field and click **Test voice** to preview.
6. All changes are saved automatically.

### Changing the Keyboard Shortcut

1. Open **Advanced options → Keyboard shortcut**.
2. Click **Change**.
3. Press your desired key combination (e.g., Ctrl+Shift+S).
4. The new shortcut is saved and applied to KDE global shortcuts immediately.

### Installing Piper Neural TTS

1. Select **Piper (Neural TTS)** as the engine.
2. If Piper is not installed, an installation dialog appears.
3. Click **Install** to automatically download and install via `pacman`:
   - `piper-tts-bin` — The Piper binary
   - `piper-voices-<lang>` — Voice models for your language
4. After installation, voices are discovered automatically.

## Configuration

User preferences are stored at:

```
~/.config/biglinux-tts/settings.json
```

### Settings Structure

```json
{
  "speech": {
    "backend": "speech-dispatcher",
    "output_module": "rhvoice",
    "voice_id": "Leticia-F123",
    "rate": 0,
    "pitch": 0,
    "volume": 80
  },
  "text": {
    "expand_abbreviations": true,
    "process_special_chars": false,
    "strip_formatting": true,
    "process_urls": false,
    "max_chars": 0
  },
  "shortcut": {
    "keybinding": "<Alt>v",
    "show_in_launcher": false
  }
}
```

| Setting | Description | Default |
|---|---|---|
| `backend` | TTS engine: `speech-dispatcher`, `espeak-ng`, `piper` | `speech-dispatcher` |
| `output_module` | speech-dispatcher module: `rhvoice`, `espeak-ng`, etc. | `rhvoice` |
| `voice_id` | Voice identifier (engine-specific) | Auto-detected |
| `rate` | Speech speed (-100 to 100) | `0` |
| `pitch` | Voice pitch (-100 to 100) | `0` |
| `volume` | Speech volume (0 to 100) | `80` |
| `expand_abbreviations` | Replace abbreviations with full words | `true` |
| `process_special_chars` | Read symbols (#, @, %) by name | `false` |
| `strip_formatting` | Remove HTML/Markdown before reading | `true` |
| `process_urls` | Read URLs aloud | `false` |
| `max_chars` | Character limit (0 = unlimited) | `0` |
| `keybinding` | Global keyboard shortcut | `<Alt>v` |
| `show_in_launcher` | Pin speak button to taskbar | `false` |

## Voice Discovery

The application automatically discovers voices from multiple sources:

### RHVoice
Scans `/usr/share/RHVoice/voices/` for installed voice directories. Extracts language, gender, and display name from the voice configuration.

### espeak-ng
Runs `espeak-ng --voices` and parses the output. Maps language codes to human-readable voice names with gender detection.

### Piper
Recursively scans `/usr/share/piper-voices/` for `.onnx` model files paired with `.onnx.json` configuration. Extracts language, speaker name, and quality level from the file path structure: `{lang}/{lang_REGION}/{speaker}/{quality}/{model}.onnx`.

## Translation

The application uses gettext for internationalization. Translation files are located in `locale/`.

### Supported Languages

Bulgarian, Czech, Danish, Dutch, English, Estonian, Finnish, French, German, Greek, Hebrew, Croatian, Hungarian, Icelandic, Italian, Japanese, Korean, Norwegian, Polish, Portuguese, Portuguese (Brazil), Romanian, Russian, Slovak, Swedish, Turkish, Ukrainian, Chinese.

### Adding a New Translation

1. Copy the template: `cp locale/tts-biglinux.pot locale/<lang>.po`
2. Edit the `.po` file with your translations.
3. Compile: `msgfmt locale/<lang>.po -o usr/share/locale/<lang>/LC_MESSAGES/tts-biglinux.mo`
4. Generate JSON: Convert the `.po` file to JSON for runtime use.

## Command-Line Usage

```bash
# Launch the settings GUI
biglinux-tts

# Read selected text (used by keyboard shortcut)
biglinux-tts-speak

# Read text from stdin
echo "Hello, world!" | tts-text

# Launch with debug logging
PYTHONPATH=src python3 -m biglinux_tts --debug
```

## Contributing

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m 'Add my feature'`
4. Push to the branch: `git push origin feature/my-feature`
5. Open a Pull Request.

## License

This project is licensed under the [GPL-3.0 License](LICENSE).

## Credits

- **BigLinux Team** — Distribution and packaging
- **GTK / GNOME** — UI framework
- **RHVoice** — High-quality multilingual TTS
- **espeak-ng** — Lightweight TTS engine
- **Piper** — Neural TTS by Rhasspy
- **speech-dispatcher** — TTS middleware
