"""
Voice Manager Dialog — Install / Remove TTS voice packages.

Presents a modern Adwaita interface for browsing, installing, and removing
voice packages for all TTS engines (RHVoice, Piper, espeak-ng) via pacman.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from services.kokoro_voice_service import (
    get_voice_status as kokoro_get_voice_status,
    download_voice as kokoro_download_voice,
    remove_voice as kokoro_remove_voice,
    is_kokoro_installed,
    BASE_VOICE_IDS as KOKORO_BASE_VOICE_IDS,
    get_active_voices_bin,
)
from utils.i18n import _

logger = logging.getLogger(__name__)

# ── Language / Region display names ──────────────────────────────────

_LANG_DISPLAY: dict[str, str] = {
    # RHVoice languages
    "albanian": "🇦🇱  Albanian",
    "brazilian-portuguese": "🇧🇷  Brazilian Portuguese",
    "croatian": "🇭🇷  Croatian",
    "czech": "🇨🇿  Czech",
    "english": "🇬🇧  English",
    "esperanto": "🌍  Esperanto",
    "georgian": "🇬🇪  Georgian",
    "kyrgyz": "🇰🇬  Kyrgyz",
    "macedonian": "🇲🇰  Macedonian",
    "polish": "🇵🇱  Polish",
    "russian": "🇷🇺  Russian",
    "serbian": "🇷🇸  Serbian",
    "slovak": "🇸🇰  Slovak",
    "spanish": "🇪🇸  Spanish",
    "tatar": "Tatar",
    "ukrainian": "🇺🇦  Ukrainian",
    "uzbek": "🇺🇿  Uzbek",
    # Piper locale codes → display
    "ar-jo": "🇯🇴  Arabic",
    "ca-es": "🏴󠁥󠁳󠁣󠁴󠁿  Catalan",
    "cs-cz": "🇨🇿  Czech",
    "cy-gb": "🏴  Welsh",
    "da-dk": "🇩🇰  Danish",
    "de-de": "🇩🇪  German",
    "el-gr": "🇬🇷  Greek",
    "en-gb": "🇬🇧  English (UK)",
    "en-us": "🇺🇸  English (US)",
    "es-es": "🇪🇸  Spanish (Spain)",
    "es-mx": "🇲🇽  Spanish (Mexico)",
    "fa-ir": "🇮🇷  Persian",
    "fi-fi": "🇫🇮  Finnish",
    "fr-fr": "🇫🇷  French",
    "hu-hu": "🇭🇺  Hungarian",
    "is-is": "🇮🇸  Icelandic",
    "it-it": "🇮🇹  Italian",
    "ka-ge": "🇬🇪  Georgian",
    "kk-kz": "🇰🇿  Kazakh",
    "lb-lu": "🇱🇺  Luxembourgish",
    "ne-np": "🇳🇵  Nepali",
    "nl-be": "🇧🇪  Dutch (Belgium)",
    "nl-nl": "🇳🇱  Dutch",
    "no-no": "🇳🇴  Norwegian",
    "pl-pl": "🇵🇱  Polish",
    "pt-br": "🇧🇷  Portuguese (Brazil)",
    "pt-pt": "🇵🇹  Portuguese (Portugal)",
    "ro-ro": "🇷🇴  Romanian",
    "ru-ru": "🇷🇺  Russian",
    "sk-sk": "🇸🇰  Slovak",
    "sl-si": "🇸🇮  Slovenian",
    "sr-rs": "🇷🇸  Serbian",
    "sv-se": "🇸🇪  Swedish",
    "sw-cd": "🇨🇩  Swahili",
    "tr-tr": "🇹🇷  Turkish",
    "uk-ua": "🇺🇦  Ukrainian",
    "vi-vn": "🇻🇳  Vietnamese",
    "zh-cn": "🇨🇳  Chinese",
    # Kokoro language codes
    "pt-br": "🇧🇷  Portuguese (Brazil)",
    "en-us": "🇺🇸  English (US)",
    "en-gb": "🇬🇧  English (UK)",
    "es": "🇪🇸  Spanish",
    "fr": "🇫🇷  French",
    "it": "🇮🇹  Italian",
    "ja": "🇯🇵  Japanese",
    "hi": "🇮🇳  Hindi",
    "zh": "🇨🇳  Chinese",
}

_GENDER_ICON: dict[str, str] = {
    "female": "♀",
    "male": "♂",
}

# Sample texts for voice preview, keyed by language prefix
_PREVIEW_TEXT: dict[str, str] = {
    "pt": "Olá, esta é uma demonstração de voz.",
    "en": "Hello, this is a voice demonstration.",
    "es": "Hola, esta es una demostración de voz.",
    "fr": "Bonjour, ceci est une démonstration vocale.",
    "de": "Hallo, dies ist eine Sprachdemonstration.",
    "it": "Ciao, questa è una dimostrazione vocale.",
    "ja": "こんにちは、これは音声デモンストレーションです。",
    "zh": "你好，这是语音演示。",
    "ru": "Здравствуйте, это демонстрация голоса.",
    "hi": "नमस्ते, यह एक आवाज़ प्रदर्शन है।",
    "pl": "Cześć, to jest demonstracja głosu.",
    "uk": "Привіт, це демонстрація голосу.",
    "ko": "안녕하세요, 음성 시연입니다.",
    "ar": "مرحبًا، هذا عرض صوتي.",
    "tr": "Merhaba, bu bir ses gösterisidir.",
    "nl": "Hallo, dit is een stemmonstratie.",
    "sv": "Hej, detta är en röstdemonstration.",
    "da": "Hej, dette er en stemmedemonstration.",
    "fi": "Hei, tämä on ääniesittely.",
    "no": "Hei, dette er en stemmedemonstrasjon.",
    "cs": "Ahoj, toto je ukázka hlasu.",
    "sk": "Ahoj, toto je ukážka hlasu.",
    "ro": "Bună, aceasta este o demonstrație vocală.",
    "hu": "Helló, ez egy hangbemutató.",
    "el": "Γεια σας, αυτή είναι μια ηχητική επίδειξη.",
    "hr": "Bok, ovo je demonstracija glasa.",
    "sr": "Здраво, ово је демонстрација гласа.",
    "sl": "Pozdravljeni, to je glasovna predstavitev.",
    "ka": "გამარჯობა, ეს ხმის დემონსტრაციაა.",
    "fa": "سلام، این یک نمایش صوتی است.",
    "vi": "Xin chào, đây là bản trình diễn giọng nói.",
    "cy": "Helo, dyma arddangosiad llais.",
    "is": "Halló, þetta er radddemó.",
    "sw": "Habari, hii ni onyesho la sauti.",
    "lb": "Moien, dëst ass eng Stëmmdemo.",
    "ne": "नमस्ते, यो एउटा आवाज प्रदर्शन हो।",
    "kk": "Сәлем, бұл дауыс демонстрациясы.",
    "ky": "Салам, бул үн демонстрациясы.",
    "uz": "Salom, bu ovozli namoyish.",
    "ca": "Hola, aquesta és una demostració de veu.",
}


# ── Data helpers ─────────────────────────────────────────────────────


def _query_packages(search_term: str, prefix: str) -> list[dict[str, str]]:
    """Query pacman for packages matching a search term.

    Returns a list of dicts with keys:
        pkg, display_name, language, version, installed, description, engine
    """
    packages: list[dict[str, str]] = []
    try:
        proc = subprocess.run(
            ["pacman", "-Ss", search_term],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return packages

        lines = proc.stdout.strip().splitlines()
        i = 0
        while i < len(lines):
            header = lines[i].strip()
            desc = lines[i + 1].strip() if i + 1 < len(lines) else ""
            i += 2

            # Parse: repo/pkg-name version (group) [installed]
            m = re.match(
                r"^(?:\S+/)?(\S+)\s+(\S+)(?:\s+\(.*?\))?(?:\s+\[.*?\])?\s*$",
                header,
            )
            if not m:
                continue
            pkg_name = m.group(1)
            version = m.group(2)
            installed = "[instalado]" in header or "[installed]" in header

            if not pkg_name.startswith(prefix):
                continue

            packages.append(
                {
                    "pkg": pkg_name,
                    "version": version,
                    "installed": "yes" if installed else "no",
                    "description": desc,
                }
            )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.error("Failed to query pacman for %s: %s", search_term, e)
    return packages


def _humanize_size(val: str) -> str:
    """'111.19 MiB' → '111.19 MB' (drop the binary 'i' for a friendlier label)."""
    return val.replace("MiB", "MB").replace("KiB", "KB").replace("GiB", "GB")


def _parse_pacman_sizes(cmd: list[str], field: str) -> dict[str, str]:
    """Run a pacman info command and map package name → humanized size."""
    out: dict[str, str] = {}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "LC_ALL": "C"},  # stable English field labels
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.debug("pacman size query failed: %s", e)
        return out
    name: str | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("Name"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith(field) and name:
            out[name] = _humanize_size(line.split(":", 1)[1].strip())
            name = None
    return out


def _annotate_sizes(data: dict[str, list[dict[str, str]]]) -> None:
    """Add a 'size' field (download size available, installed size for installed)
    to each package. pacman engines are queried in batch; Kokoro voices are a
    small fixed download."""
    avail_names: list[str] = []
    inst_names: list[str] = []
    by_name: dict[str, dict[str, str]] = {}

    for engine, pkgs in data.items():
        if engine == "Kokoro":
            for p in pkgs:
                if p.get("installed") == "no":
                    p["size"] = "≈ 0.5 MB"
            continue
        for p in pkgs:
            by_name[p["pkg"]] = p
            (inst_names if p.get("installed") == "yes" else avail_names).append(p["pkg"])

    sizes: dict[str, str] = {}
    if avail_names:
        sizes.update(_parse_pacman_sizes(["pacman", "-Si", *avail_names], "Download Size"))
    if inst_names:
        sizes.update(_parse_pacman_sizes(["pacman", "-Qi", *inst_names], "Installed Size"))
    for name, sz in sizes.items():
        if name in by_name:
            by_name[name]["size"] = sz


def _query_all_voice_packages() -> dict[str, list[dict[str, str]]]:
    """Query pacman for all voice packages across all engines.

    Returns dict keyed by engine name: {"RHVoice": [...], "Piper": [...], "espeak-ng": [...]}
    """
    result: dict[str, list[dict[str, str]]] = {}

    # ── RHVoice voices ──
    rhvoice_pkgs = _query_packages("rhvoice-voice-", "rhvoice-voice-")
    for pkg in rhvoice_pkgs:
        voice_name = pkg["pkg"].removeprefix("rhvoice-voice-")
        lang_match = re.search(r"for\s+(\S+(?:-\S+)?)\s+language", pkg["description"], re.I)
        language = lang_match.group(1).lower() if lang_match else "unknown"

        pkg["voice_name"] = voice_name
        pkg["display_name"] = voice_name.replace("-", " ").title()
        pkg["language"] = language
        pkg["engine"] = "RHVoice"
        pkg["gender"] = _guess_gender(voice_name)

    if rhvoice_pkgs:
        result["RHVoice"] = rhvoice_pkgs

    # ── Piper voices ──
    piper_pkgs = _query_packages("piper-voices-", "piper-voices-")
    # Filter out "piper-voices-common"
    piper_pkgs = [p for p in piper_pkgs if p["pkg"] != "piper-voices-common"]
    for pkg in piper_pkgs:
        locale = pkg["pkg"].removeprefix("piper-voices-")
        pkg["voice_name"] = locale
        pkg["display_name"] = _LANG_DISPLAY.get(locale.lower(), locale.upper())
        pkg["language"] = locale.lower()
        pkg["engine"] = "Piper"
        pkg["gender"] = ""

    if piper_pkgs:
        result["Piper"] = piper_pkgs

    # ── espeak-ng (single package) ──
    espeak_pkgs = _query_packages("espeak-ng", "espeak-ng")
    # Only the main espeak-ng package, not espeakup etc.
    espeak_pkgs = [p for p in espeak_pkgs if p["pkg"] == "espeak-ng"]
    for pkg in espeak_pkgs:
        pkg["voice_name"] = "espeak-ng"
        pkg["display_name"] = "espeak-ng"
        pkg["language"] = "multi"
        pkg["engine"] = "espeak-ng"
        pkg["gender"] = ""

    if espeak_pkgs:
        result["espeak-ng"] = espeak_pkgs

    # ── Also check piper-tts-bin ──
    piper_engine_pkgs = _query_packages("piper-tts", "piper-tts")
    for pkg in piper_engine_pkgs:
        pkg["voice_name"] = "piper-tts"
        pkg["display_name"] = "Piper TTS Engine"
        pkg["language"] = ""
        pkg["engine"] = "Piper (Engine)"
        pkg["gender"] = ""
    if piper_engine_pkgs:
        result.setdefault("Piper", [])
        # Prepend engine packages
        result["Piper"] = piper_engine_pkgs + result["Piper"]

    # ── Kokoro TTS — individual voices ──
    kokoro_voices = kokoro_get_voice_status()
    if kokoro_voices:
        result["Kokoro"] = kokoro_voices

    return result


def _guess_gender(name: str) -> str:
    """Best-effort gender guess from voice name."""
    female = {
        "leticia", "natalia", "anna", "elena", "irina", "lyubov",
        "marianna", "hana", "suze", "magda", "clb", "slt", "spomenka",
        "natia", "arina", "tatiana", "victoria", "alicja", "karmela",
        "dragana", "nazgul", "dilnavoz", "sevinch", "jasietka",
    }
    name_lower = name.lower().replace("-", "")
    for f in female:
        if f in name_lower:
            return "female"
    return "male"


# ── Dialog ───────────────────────────────────────────────────────────


class VoiceManagerDialog(Adw.Dialog):
    """Full-screen Adwaita dialog for managing TTS voice packages."""

    def __init__(
        self,
        on_voices_changed: Callable[[], None] | None = None,
        engine_filter: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._on_voices_changed = on_voices_changed
        self._engine_filter = engine_filter
        self._all_packages: dict[str, list[dict[str, str]]] = {}
        self._busy = False
        self._preview_proc: subprocess.Popen | None = None
        self._preview_tmp: str | None = None
        self._cancel_download = threading.Event()

        self.set_title(_("Voice Manager"))
        self.set_content_width(580)
        self.set_content_height(680)

        # Main layout
        toolbarview = Adw.ToolbarView()

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        toolbarview.add_top_bar(header)

        # ── Search bar (filters the voice list) ──
        self._search_query = ""
        self._search_debounce_id = 0
        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        search_bar.set_margin_start(12)
        search_bar.set_margin_end(12)
        search_bar.set_margin_top(6)
        search_bar.set_margin_bottom(6)
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text(_("Search voices by name or language…"))
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("search-changed", self._on_search_changed)
        search_bar.append(self._search_entry)
        toolbarview.add_top_bar(search_bar)

        # Scrollable content
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_vexpand(True)

        # Container
        self._content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
        )
        self._content_box.set_margin_start(16)
        self._content_box.set_margin_end(16)
        self._content_box.set_margin_top(12)
        self._content_box.set_margin_bottom(24)

        # Loading state
        self._loading_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
        )
        self._loading_box.set_valign(Gtk.Align.CENTER)
        self._loading_box.set_vexpand(True)
        self._spinner = Gtk.Spinner()
        self._spinner.set_size_request(32, 32)
        self._spinner.set_halign(Gtk.Align.CENTER)
        self._loading_box.append(self._spinner)
        loading_label = Gtk.Label(label=_("Loading available voices…"))
        loading_label.add_css_class("dim-label")
        self._loading_box.append(loading_label)

        # Stack
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(self._loading_box, "loading")
        self._scroll.set_child(self._content_box)
        self._stack.add_named(self._scroll, "content")

        toolbarview.set_content(self._stack)

        # Thin OSD progress bar overlaid at the very top of the dialog, shown
        # only while installing/downloading (no trough, minimal height).
        overlay = Gtk.Overlay()
        overlay.set_child(toolbarview)
        self._progress = Gtk.ProgressBar()
        self._progress.add_css_class("osd")
        self._progress.set_valign(Gtk.Align.START)
        self._progress.set_halign(Gtk.Align.FILL)
        self._progress.set_visible(False)
        overlay.add_overlay(self._progress)
        self.set_child(overlay)

        # Start loading
        self._stack.set_visible_child_name("loading")
        self._spinner.start()
        self.connect("closed", lambda _d: (self._stop_preview(), self._cancel_download.set()))
        threading.Thread(target=self._load_packages, daemon=True).start()

    # ── Loading ──────────────────────────────────────────────────────

    def _load_packages(self) -> None:
        """Load all packages (with sizes) in background."""
        data = _query_all_voice_packages()
        _annotate_sizes(data)
        GLib.idle_add(self._populate, data)

    def _populate(self, data: dict[str, list[dict[str, str]]]) -> None:
        """Populate UI (main thread)."""
        self._all_packages = data
        self._spinner.stop()

        if not data:
            self._show_empty_state()
        else:
            self._rebuild_list()

        self._stack.set_visible_child_name("content")

    def _show_empty_state(self) -> None:
        """Show when no packages found."""
        self._clear_content()
        status = Adw.StatusPage()
        status.set_icon_name("dialog-warning-symbolic")
        status.set_title(_("No Voice Packages Found"))
        status.set_description(
            _("Could not find any TTS voice packages from pacman repositories.")
        )
        self._content_box.append(status)

    def _clear_content(self) -> None:
        """Remove all children."""
        child = self._content_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._content_box.remove(child)
            child = nxt

    # ── Build UI ─────────────────────────────────────────────────────

    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        """Debounced search — rebuild the list filtered by the query."""
        if self._search_debounce_id:
            GLib.source_remove(self._search_debounce_id)
        self._search_debounce_id = GLib.timeout_add(220, self._run_search)

    def _run_search(self) -> bool:
        self._search_debounce_id = 0
        self._search_query = self._search_entry.get_text().strip().lower()
        if self._all_packages:
            self._rebuild_list()
        return False

    def _matches_search(self, pkg: dict[str, str]) -> bool:
        """Whether a package matches the current search query."""
        if not self._search_query:
            return True
        q = self._search_query
        hay = " ".join(
            str(pkg.get(k, "")) for k in ("display_name", "language", "voice_name", "pkg")
        ).lower()
        lang = pkg.get("language", "")
        hay += " " + _LANG_DISPLAY.get(lang.lower(), lang).lower()
        return q in hay

    def _rebuild_list(self) -> None:
        """Build the full engine-grouped voice list (respecting the search)."""
        self._clear_content()

        engine_meta = {
            "RHVoice": {
                "icon": "audio-speakers-symbolic",
                "subtitle": _("High quality offline voices"),
            },
            "Piper": {
                "icon": "starred-symbolic",
                "subtitle": _("Neural network voices — natural sounding"),
            },
            "espeak-ng": {
                "icon": "audio-card-symbolic",
                "subtitle": _("Lightweight multi-language synthesizer"),
            },
            "Kokoro": {
                "icon": "starred-symbolic",
                "subtitle": _("Neural TTS — high quality multilingual voices (82M)"),
            },
        }

        for engine_name in ["Kokoro", "RHVoice", "Piper", "espeak-ng"]:
            # Apply engine filter if specified
            if self._engine_filter and self._engine_filter.lower() not in engine_name.lower():
                continue

            pkgs = self._all_packages.get(engine_name, [])
            if not pkgs:
                continue

            meta = engine_meta.get(engine_name, {})
            installed = [p for p in pkgs if p["installed"] == "yes" and self._matches_search(p)]
            available = [p for p in pkgs if p["installed"] == "no" and self._matches_search(p)]

            if not installed and not available:
                continue  # nothing matches the search in this engine

            # ── Engine group ──
            group = Adw.PreferencesGroup()
            group.set_title(engine_name)
            group.set_description(meta.get("subtitle", ""))

            # ── Installed sub-section ──
            if installed:
                for pkg in sorted(installed, key=lambda p: p.get("display_name", "")):
                    row = self._make_row(pkg, is_installed=True)
                    group.add(row)

            # ── Available voices — listed directly (no expanders) ──
            def _sort_key(p: dict[str, str]) -> tuple[str, str]:
                lang = p.get("language", "")
                return (
                    _LANG_DISPLAY.get(lang.lower(), lang).lower(),
                    p.get("display_name", "").lower(),
                )

            for pkg in sorted(available, key=_sort_key):
                group.add(self._make_row(pkg, is_installed=False))

            self._content_box.append(group)

    def _make_row(
        self, pkg: dict[str, str], *, is_installed: bool
    ) -> Adw.ActionRow:
        """Create a row with install/remove button."""
        row = Adw.ActionRow()

        gender = pkg.get("gender", "")
        gender_icon = _GENDER_ICON.get(gender, "")
        display = pkg.get("display_name", pkg["pkg"])

        if gender_icon:
            row.set_title(f"{gender_icon}  {display}")
        else:
            row.set_title(display)

        # Subtitle: language • version • size (download size before install,
        # installed size once installed). Piper titles already carry the
        # language, so skip it there — but always show the size.
        lang = pkg.get("language", "")
        lang_display = _LANG_DISPLAY.get(lang, lang.title()) if lang else ""
        sub_parts: list[str] = []
        if (
            lang_display
            and lang_display.strip().lower() != display.strip().lower()
            and pkg.get("engine") != "Piper"
        ):
            sub_parts.append(lang_display)
        if is_installed and pkg.get("version"):
            sub_parts.append(pkg["version"])
        if pkg.get("size"):
            sub_parts.append(pkg["size"])
        if sub_parts:
            row.set_subtitle("  •  ".join(sub_parts))

        engine = pkg.get("engine", "")
        is_kokoro_base = engine == "Kokoro" and pkg.get("is_base") == "yes"
        no_remove = ("espeak-ng", "piper-tts-bin", "piper-voices-common")
        removable = is_installed and pkg["pkg"] not in no_remove and not is_kokoro_base

        # Preview button for installed voices on engines with direct CLI access.
        if is_installed and engine in ("Kokoro", "RHVoice", "espeak-ng"):
            preview_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
            preview_btn.add_css_class("flat")
            preview_btn.add_css_class("circular")
            preview_btn.set_valign(Gtk.Align.CENTER)
            preview_btn.set_tooltip_text(_("Preview voice"))
            preview_btn.connect("clicked", lambda b, p=pkg: self._on_preview(b, p))
            row.add_suffix(preview_btn)

        # Single action icon that alternates install ⇄ uninstall — no text tag.
        if not is_installed:
            btn = Gtk.Button(icon_name="folder-download-symbolic")
            btn.add_css_class("circular")
            btn.add_css_class("suggested-action")
            btn.set_valign(Gtk.Align.CENTER)
            btn.set_tooltip_text(_("Install {name}").format(name=display))
            btn.connect("clicked", lambda b, p=pkg: self._on_install(b, p))
            row.add_suffix(btn)
        elif removable:
            btn = Gtk.Button(icon_name="user-trash-symbolic")
            btn.add_css_class("circular")
            btn.add_css_class("destructive-action")
            btn.set_valign(Gtk.Align.CENTER)
            btn.set_tooltip_text(_("Remove {name}").format(name=display))
            btn.connect("clicked", lambda b, p=pkg: self._on_remove(b, p))
            row.add_suffix(btn)
        else:
            # Installed but not removable (core engine / base voice): a quiet
            # check mark, no "Installed" text tag.
            check = Gtk.Image.new_from_icon_name("object-select-symbolic")
            check.add_css_class("dim-label")
            check.set_valign(Gtk.Align.CENTER)
            check.set_tooltip_text(_("Installed"))
            row.add_suffix(check)

        return row

    # ── Actions ──────────────────────────────────────────────────────

    def _on_install(self, button: Gtk.Button, pkg: dict[str, str]) -> None:
        """Install a package directly (no confirmation)."""
        if self._busy:
            return
        self._run_action("install", pkg, button)

    def _on_remove(self, button: Gtk.Button, pkg: dict[str, str]) -> None:
        """Remove a package directly (no confirmation)."""
        if self._busy:
            return
        self._run_action("remove", pkg, button)

    def _confirm(
        self,
        heading: str,
        body: str,
        confirm_label: str,
        appearance: Adw.ResponseAppearance,
        on_confirm: Callable[[], None],
    ) -> None:
        """Confirmation dialog."""
        dialog = Adw.AlertDialog()
        dialog.set_heading(heading)
        dialog.set_body(body)
        dialog.set_body_use_markup(True)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("confirm", confirm_label)
        dialog.set_response_appearance("confirm", appearance)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _on_resp(d: Adw.AlertDialog, response: str) -> None:
            if response == "confirm":
                on_confirm()

        dialog.connect("response", _on_resp)
        parent = self.get_root()
        dialog.present(parent if parent else self)

    def _run_action(
        self, action: str, pkg: dict[str, str], button: Gtk.Button
    ) -> None:
        """Execute pacman install/remove in background."""
        self._busy = True
        button.set_sensitive(False)

        # Busy spinner in the row's action slot; the actual progress is shown by
        # the thin OSD bar overlaid at the top of the dialog.
        spinner = Gtk.Spinner()
        spinner.set_size_request(16, 16)
        spinner.start()
        old_child = button.get_child()
        button.set_child(spinner)

        # Thin OSD bar only — no text.
        self._progress.set_show_text(False)
        self._progress.set_fraction(0.0)
        self._progress.set_visible(True)

        pkg_name = pkg["pkg"]
        is_kokoro = pkg.get("engine") == "Kokoro"
        is_download = is_kokoro and action == "install"
        state = {"pulse_id": 0}

        progress_cb = None
        if is_download:
            self._cancel_download.clear()

            def progress_cb(downloaded: int, total: int) -> None:
                def _update() -> bool:
                    if total > 0:
                        self._progress.set_fraction(min(1.0, downloaded / total))
                    else:
                        self._progress.pulse()
                    return False

                GLib.idle_add(_update)
        else:
            # pacman install/remove has no byte-level progress → pulse the bar.
            def _pulse() -> bool:
                self._progress.pulse()
                return True

            state["pulse_id"] = GLib.timeout_add(120, _pulse)

        def _worker() -> tuple[bool, str]:
            try:
                if is_kokoro:
                    # Kokoro: download/remove individual voice files
                    voice_id = pkg.get("voice_id", "")
                    if action == "install":
                        return kokoro_download_voice(
                            voice_id,
                            progress_cb=progress_cb,
                            cancel_check=self._cancel_download.is_set,
                        )
                    else:
                        return kokoro_remove_voice(voice_id)

                if action == "install":
                    cmd = [
                        "pkexec", "pacman", "-S", "--noconfirm",
                        "--needed", pkg_name,
                    ]
                else:
                    cmd = [
                        "pkexec", "pacman", "-R", "--noconfirm", pkg_name,
                    ]

                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                if proc.returncode == 0:
                    return True, ""
                err = proc.stderr.strip() or proc.stdout.strip()
                return False, err
            except subprocess.TimeoutExpired:
                return False, _("Operation timed out")
            except FileNotFoundError:
                return False, _("pkexec or pacman not found")
            except Exception as e:
                return False, str(e)

        def _on_done(result: tuple[bool, str]) -> bool:
            success, error = result
            self._busy = False
            if state["pulse_id"]:
                GLib.source_remove(state["pulse_id"])
                state["pulse_id"] = 0
            self._progress.set_visible(False)
            button.set_child(old_child)
            button.set_sensitive(True)

            if success:
                # Reload the full list
                self._spinner.start()
                self._stack.set_visible_child_name("loading")
                threading.Thread(target=self._load_packages, daemon=True).start()

                if self._on_voices_changed:
                    self._on_voices_changed()
            elif error == "cancelled":
                # User cancelled (e.g. closed the dialog) — no error popup.
                pass
            else:
                err_dialog = Adw.AlertDialog()
                err_dialog.set_heading(
                    _("Failed") if action == "install"
                    else _("Removal Failed")
                )
                err_dialog.set_body(error[:500] if error else _("Unknown error"))
                err_dialog.add_response("ok", _("OK"))
                parent = self.get_root()
                err_dialog.present(parent if parent else self)

            return False

        def _threaded() -> None:
            result = _worker()
            GLib.idle_add(_on_done, result)

        threading.Thread(target=_threaded, daemon=True).start()

    # ── Voice preview ────────────────────────────────────────────────

    def _stop_preview(self) -> None:
        """Kill any running preview subprocess and cleanup temp files."""
        if self._preview_proc and self._preview_proc.poll() is None:
            self._preview_proc.terminate()
            try:
                self._preview_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._preview_proc.kill()
        self._preview_proc = None
        if self._preview_tmp:
            try:
                os.unlink(self._preview_tmp)
            except OSError:
                pass
            self._preview_tmp = None

    def _get_sample_text(self, lang: str) -> str:
        """Get sample text for a language code.

        Never silently falls back to English: for an unknown/empty/multi
        language, use the system locale's sample instead.
        """
        lang_lower = (lang or "").lower().replace("_", "-")
        if lang_lower and lang_lower in _PREVIEW_TEXT:
            return _PREVIEW_TEXT[lang_lower]
        prefix = lang_lower.split("-")[0] if lang_lower else ""
        if prefix and prefix in _PREVIEW_TEXT:
            return _PREVIEW_TEXT[prefix]
        # Unknown/empty/"multi" → prefer the system language over English.
        from services.text_processor import get_system_language

        sys_lang = get_system_language()
        if sys_lang in _PREVIEW_TEXT:
            return _PREVIEW_TEXT[sys_lang]
        return _PREVIEW_TEXT.get("en", next(iter(_PREVIEW_TEXT.values())))

    @staticmethod
    def _espeak_voice_for_lang(lang: str) -> str:
        """Map a voice language to an espeak-ng voice code (never default en)."""
        from services.text_processor import get_system_language

        loc = (lang or "").lower().replace("_", "-")
        if not loc or loc in ("multi", "unknown"):
            sys_lang = get_system_language()
            return "pt-br" if sys_lang == "pt" else (sys_lang or "pt-br")
        if loc.startswith("pt"):
            return "pt-br" if "br" in loc else "pt"
        return loc

    def _on_preview(self, button: Gtk.Button, pkg: dict[str, str]) -> None:
        """Preview an installed voice."""
        self._stop_preview()

        engine = pkg.get("engine", "")
        lang = pkg.get("language", "")
        sample = self._get_sample_text(lang)

        # Show spinner feedback on button
        button.set_icon_name("media-playback-stop-symbolic")

        def _restore_button() -> bool:
            button.set_icon_name("media-playback-start-symbolic")
            return False

        if engine == "espeak-ng":
            self._preview_espeak(sample, _restore_button, lang)
        elif engine == "RHVoice":
            voice_name = pkg.get("voice_name", "")
            self._preview_rhvoice(voice_name, sample, _restore_button)
        elif engine == "Kokoro":
            voice_id = pkg.get("voice_id", "")
            self._preview_kokoro(voice_id, lang, sample, _restore_button)
        else:
            _restore_button()

    def _preview_espeak(
        self, text: str, on_done: Callable[[], bool], lang: str = ""
    ) -> None:
        """Preview using espeak-ng (speaks directly, no temp file).

        Always passes an explicit voice so the preview never uses espeak's
        default English voice for a non-English sample.
        """
        voice = self._espeak_voice_for_lang(lang)

        def _worker() -> None:
            try:
                cmd = ["espeak-ng"]
                if voice:
                    cmd += ["-v", voice]
                cmd.append(text)
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._preview_proc = proc
                proc.wait()
            except (FileNotFoundError, OSError) as e:
                logger.warning("espeak-ng preview failed: %s", e)
            GLib.idle_add(on_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _preview_rhvoice(
        self, voice_name: str, text: str, on_done: Callable[[], bool]
    ) -> None:
        """Preview via speech-dispatcher with RHVoice output module."""
        def _worker() -> None:
            try:
                cmd = ["spd-say", "-o", "rhvoice", "-y", voice_name, "-w", text]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._preview_proc = proc
                proc.wait()
            except (FileNotFoundError, OSError) as e:
                logger.warning("RHVoice preview failed: %s", e)
            GLib.idle_add(on_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _preview_kokoro(
        self, voice_id: str, lang: str, text: str,
        on_done: Callable[[], bool],
    ) -> None:
        """Preview via koko CLI binary."""
        koko_bin = shutil.which("koko")
        if not koko_bin:
            logger.warning("koko binary not found for preview")
            GLib.idle_add(on_done)
            return

        # Map language to koko lang code
        lang_lower = lang.lower().replace("_", "-")
        lang_code = lang_lower if lang_lower else "pt-br"

        def _worker() -> None:
            tmp_path = None
            try:
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp_path = tmp.name
                tmp.close()
                self._preview_tmp = tmp_path

                koko_env = {
                    **os.environ,
                    "KOKO_MODEL_PATH": "/usr/share/biglinux-kokoro-tts/model/model.onnx",
                    "KOKO_DATA_PATH": str(get_active_voices_bin()),
                }

                gen_cmd = [
                    koko_bin, "-s", voice_id, "-l", lang_code,
                    "--force-style", "true",
                    "text", "-o", tmp_path, text,
                ]
                gen_proc = subprocess.Popen(
                    gen_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=koko_env,
                )
                self._preview_proc = gen_proc
                gen_proc.wait()

                if gen_proc.returncode != 0:
                    logger.warning("Kokoro preview gen failed (code %d)", gen_proc.returncode)
                    GLib.idle_add(on_done)
                    return

                # Play generated audio
                play_proc = subprocess.Popen(
                    ["aplay", "-q", tmp_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._preview_proc = play_proc
                play_proc.wait()
            except (FileNotFoundError, OSError) as e:
                logger.warning("Kokoro preview failed: %s", e)
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    self._preview_tmp = None
            GLib.idle_add(on_done)

        threading.Thread(target=_worker, daemon=True).start()
