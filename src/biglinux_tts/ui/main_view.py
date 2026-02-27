"""
Main view — unified settings page for BigLinux TTS.

Layout:
1. Hero section (status indicator + test button)
2. Quick settings (voice, speed, pitch, volume)
3. Expanders (backend, text processing, shortcut, advanced)
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from biglinux_tts.config import (
    PITCH_DEFAULT,
    PITCH_MAX,
    PITCH_MIN,
    PITCH_STEP,
    RATE_DEFAULT,
    RATE_MAX,
    RATE_MIN,
    RATE_STEP,
    TTSBackend,
    TTSState,
    VOLUME_DEFAULT,
    VOLUME_MAX,
    VOLUME_MIN,
    VOLUME_STEP,
)
from biglinux_tts.services.text_processor import get_system_language
from biglinux_tts.services.voice_manager import (
    VoiceCatalog,
    VoiceInfo,
    discover_voices,
    get_default_voice_for_language,
)
from biglinux_tts.ui.components import (
    create_action_row_with_scale,
    create_action_row_with_switch,
    create_button_row,
    create_combo_row,
    create_expander_row,
    create_preferences_group,
    create_spin_row,
)
from biglinux_tts.utils.async_utils import run_in_thread
from biglinux_tts.utils.i18n import _

if TYPE_CHECKING:
    from biglinux_tts.config import AppSettings
    from biglinux_tts.services.settings_service import SettingsService
    from biglinux_tts.services.tts_service import TTSService

logger = logging.getLogger(__name__)


class MainView(Adw.NavigationPage):
    """
    Main settings view with hero, quick settings, and expanders.

    Follows progressive disclosure: basic on top, advanced in expanders.
    """

    def __init__(
        self,
        tts_service: TTSService,
        settings_service: SettingsService,
        on_toast: Callable[[str, int], None],
    ) -> None:
        super().__init__()
        self.set_title(_("BigLinux TTS"))

        self._tts = tts_service
        self._settings_service = settings_service
        self._settings: AppSettings = settings_service.get()
        self._on_toast = on_toast
        self._catalog: VoiceCatalog | None = None
        self._voice_list: list[VoiceInfo] = []
        self._updating_ui = False  # Prevent feedback loops

        # Connect TTS state changes
        self._tts.set_on_state_changed(self._on_tts_state_changed)

        # Build UI
        self._build_ui()

        # Discover voices in background
        run_in_thread(discover_voices, on_done=self._on_voices_discovered)

    # ── UI Construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Build the complete main view."""
        # Scrollable content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        # Clamp for responsive width
        clamp = Adw.Clamp()
        clamp.set_maximum_size(600)
        clamp.set_tightening_threshold(400)

        # Main vertical box
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(12)
        content.set_margin_bottom(24)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # 1. Hero section
        self._hero = self._build_hero_section()
        content.append(self._hero)

        # 2. Quick settings
        quick = self._build_quick_settings()
        content.append(quick)

        # 3. Backend expander
        backend_group = self._build_backend_section()
        content.append(backend_group)

        # 4. Text processing expander
        text_group = self._build_text_processing_section()
        content.append(text_group)

        # 5. Advanced expander
        advanced_group = self._build_advanced_section()
        content.append(advanced_group)

        clamp.set_child(content)
        scrolled.set_child(clamp)
        self.set_child(scrolled)

    # ── Hero Section ─────────────────────────────────────────────────

    def _build_hero_section(self) -> Gtk.Box:
        """Build the hero status section with indicator and test button."""
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        hero.add_css_class("hero-section")
        hero.set_halign(Gtk.Align.FILL)

        # Status icon
        self._hero_icon = Gtk.Image.new_from_icon_name("audio-speakers-symbolic")
        self._hero_icon.set_pixel_size(48)
        self._hero_icon.add_css_class("hero-status-icon")
        self._hero_icon.set_halign(Gtk.Align.CENTER)
        hero.append(self._hero_icon)

        # Status title
        self._hero_title = Gtk.Label()
        self._hero_title.set_markup(f"<b>{_('Ready to speak')}</b>")
        self._hero_title.add_css_class("hero-title")
        self._hero_title.set_halign(Gtk.Align.CENTER)
        hero.append(self._hero_title)

        # Status subtitle (instructions)
        self._hero_subtitle = Gtk.Label(
            label=_("Select text and press Alt+V to read aloud")
        )
        self._hero_subtitle.add_css_class("hero-subtitle")
        self._hero_subtitle.set_halign(Gtk.Align.CENTER)
        self._hero_subtitle.set_wrap(True)
        self._hero_subtitle.set_justify(Gtk.Justification.CENTER)
        hero.append(self._hero_subtitle)

        # Test text entry
        self._test_entry = Gtk.Entry()
        self._test_entry.set_text(_("This is a test, welcome to BigLinux!"))
        self._test_entry.set_placeholder_text(_("Type text to test…"))
        self._test_entry.set_halign(Gtk.Align.FILL)
        self._test_entry.set_hexpand(True)
        self._test_entry.set_margin_start(24)
        self._test_entry.set_margin_end(24)
        self._test_entry.set_margin_top(8)
        self._test_entry.set_max_length(500)
        hero.append(self._test_entry)

        # Test button
        self._test_button = create_button_row(
            label=_("Test voice"),
            style_class="suggested-action",
            on_clicked=self._on_test_voice,
            accessible_name=_("Test the selected voice"),
        )
        self._test_button.add_css_class("test-button")
        self._test_button.set_halign(Gtk.Align.CENTER)
        self._test_button.set_margin_top(6)
        hero.append(self._test_button)

        return hero

    # ── Quick Settings ───────────────────────────────────────────────

    def _build_quick_settings(self) -> Adw.PreferencesGroup:
        """Build the voice and speech parameter controls."""
        group = create_preferences_group(
            title=_("Voice settings"),
            description=_("Choose voice and adjust speech parameters"),
        )

        # Voice selection combo
        self._voice_combo = create_combo_row(
            title=_("Voice"),
            subtitle=_("Loading voices..."),
            options=[_("Detecting installed voices...")],
            on_selected=self._on_voice_selected,
            accessible_name=_("Select TTS voice"),
        )
        group.add(self._voice_combo)

        # Speed
        self._speed_row, self._speed_scale = create_action_row_with_scale(
            title=_("Speed"),
            subtitle=_("Speech rate"),
            min_value=RATE_MIN,
            max_value=RATE_MAX,
            value=self._settings.speech.rate,
            step=RATE_STEP,
            digits=0,
            on_changed=self._on_rate_changed,
            marks=[(RATE_MIN, _("Slow")), (0, _("Normal")), (RATE_MAX, _("Fast"))],
            accessible_name=_("Speech speed"),
        )
        group.add(self._speed_row)

        # Pitch
        self._pitch_row, self._pitch_scale = create_action_row_with_scale(
            title=_("Pitch"),
            subtitle=_("Voice tone"),
            min_value=PITCH_MIN,
            max_value=PITCH_MAX,
            value=self._settings.speech.pitch,
            step=PITCH_STEP,
            digits=0,
            on_changed=self._on_pitch_changed,
            marks=[(PITCH_MIN, _("Low")), (0, _("Normal")), (PITCH_MAX, _("High"))],
            accessible_name=_("Voice pitch"),
        )
        group.add(self._pitch_row)

        # Volume
        self._volume_row, self._volume_scale = create_action_row_with_scale(
            title=_("Volume"),
            subtitle=_("Speech volume"),
            min_value=VOLUME_MIN,
            max_value=VOLUME_MAX,
            value=self._settings.speech.volume,
            step=VOLUME_STEP,
            digits=0,
            on_changed=self._on_volume_changed,
            marks=[(VOLUME_MIN, _("Mute")), (50, "50%"), (VOLUME_MAX, _("Max"))],
            accessible_name=_("Speech volume"),
        )
        group.add(self._volume_row)

        return group

    # ── Backend Section ──────────────────────────────────────────────

    def _build_backend_section(self) -> Adw.PreferencesGroup:
        """Build TTS backend selection and status."""
        group = create_preferences_group(
            title=_("TTS Engine"),
            description=_("Choose which text-to-speech engine to use"),
        )

        # Backend expander
        self._backend_expander = create_expander_row(
            title=_("Engine"),
            subtitle=_("speech-dispatcher"),
            icon_name="applications-multimedia-symbolic",
        )

        # Backend selection
        backends = [
            "speech-dispatcher (RHVoice, espeak)",
            "espeak-ng",
            "Piper (Neural TTS)",
        ]
        backend_map = {
            0: TTSBackend.SPEECH_DISPATCHER.value,
            1: TTSBackend.ESPEAK_NG.value,
            2: TTSBackend.PIPER.value,
        }
        # Find current index
        current_backend = self._settings.speech.backend
        current_idx = 0
        for idx, val in backend_map.items():
            if val == current_backend:
                current_idx = idx
                break

        self._backend_combo = create_combo_row(
            title=_("TTS Backend"),
            subtitle=_("Engine used for speech synthesis"),
            options=backends,
            selected_index=current_idx,
            on_selected=self._on_backend_selected,
            accessible_name=_("Select TTS engine"),
        )
        self._backend_expander.add_row(self._backend_combo)

        # Output module for speech-dispatcher
        modules = ["rhvoice", "espeak-ng", "espeak", "pico", "festival"]
        current_mod = self._settings.speech.output_module
        mod_idx = 0
        for i, m in enumerate(modules):
            if m == current_mod:
                mod_idx = i
                break

        self._module_combo = create_combo_row(
            title=_("Output module"),
            subtitle=_("Speech synthesizer used by speech-dispatcher"),
            options=modules,
            selected_index=mod_idx,
            on_selected=self._on_module_selected,
            accessible_name=_("Select output module"),
        )
        self._backend_expander.add_row(self._module_combo)

        # Status row
        self._backend_status = Adw.ActionRow()
        self._backend_status.set_title(_("Status"))
        self._backend_status.set_subtitle(_("Checking..."))
        self._backend_status.set_icon_name("emblem-system-symbolic")
        self._backend_expander.add_row(self._backend_status)

        group.add(self._backend_expander)

        # Check backend availability in background
        GLib.idle_add(self._check_backend_status)

        return group

    # ── Text Processing Section ──────────────────────────────────────

    def _build_text_processing_section(self) -> Adw.PreferencesGroup:
        """Build text processing options."""
        group = create_preferences_group(
            title=_("Text processing"),
            description=_("How text is cleaned and prepared before reading"),
        )

        expander = create_expander_row(
            title=_("Processing options"),
            subtitle=_("Abbreviations, formatting, limits"),
            icon_name="document-edit-symbolic",
        )

        # Expand abbreviations
        abbr_row, self._abbr_switch = create_action_row_with_switch(
            title=_("Expand abbreviations"),
            subtitle=_("Replace common abbreviations with full words"),
            active=self._settings.text.expand_abbreviations,
            on_toggled=self._on_abbreviations_toggled,
            accessible_name=_("Expand text abbreviations"),
        )
        expander.add_row(abbr_row)

        # Process special characters
        chars_row, self._chars_switch = create_action_row_with_switch(
            title=_("Read special characters"),
            subtitle=_("Speak symbols like # @ % by name"),
            active=self._settings.text.process_special_chars,
            on_toggled=self._on_special_chars_toggled,
            accessible_name=_("Read special characters aloud"),
        )
        expander.add_row(chars_row)

        # Strip formatting
        fmt_row, self._fmt_switch = create_action_row_with_switch(
            title=_("Remove formatting"),
            subtitle=_("Strip Markdown and HTML before reading"),
            active=self._settings.text.strip_formatting,
            on_toggled=self._on_strip_formatting_toggled,
            accessible_name=_("Remove text formatting"),
        )
        expander.add_row(fmt_row)

        # Process URLs
        url_row, self._url_switch = create_action_row_with_switch(
            title=_("Read URLs"),
            subtitle=_("Read web addresses aloud instead of skipping them"),
            active=self._settings.text.process_urls,
            on_toggled=self._on_urls_toggled,
            accessible_name=_("Read URLs aloud"),
        )
        expander.add_row(url_row)

        # Max characters — combo with presets
        char_options = [
            _("Unlimited"),
            "1 000",
            "5 000",
            "10 000",
            "50 000",
            "100 000",
        ]
        self._char_limit_values = [0, 1000, 5000, 10000, 50000, 100000]

        current_limit = self._settings.text.max_chars
        current_char_idx = 0  # default: Unlimited
        for i, val in enumerate(self._char_limit_values):
            if val == current_limit:
                current_char_idx = i
                break

        self._max_chars_combo = create_combo_row(
            title=_("Character limit"),
            subtitle=_("Maximum number of characters to read at once"),
            options=char_options,
            selected_index=current_char_idx,
            on_selected=self._on_max_chars_selected,
            accessible_name=_("Maximum character limit"),
        )
        expander.add_row(self._max_chars_combo)

        group.add(expander)
        return group

    # ── Advanced Section ─────────────────────────────────────────────

    def _build_advanced_section(self) -> Adw.PreferencesGroup:
        """Build advanced settings with functional shortcut editor."""
        group = create_preferences_group(
            title=_("Advanced"),
        )

        expander = create_expander_row(
            title=_("Advanced options"),
            subtitle=_("Shortcut, behavior settings"),
            icon_name="preferences-other-symbolic",
        )

        # ── Shortcut editor ──
        shortcut_row = Adw.ActionRow()
        shortcut_row.set_title(_("Keyboard shortcut"))
        shortcut_row.set_subtitle(
            _("Select text anywhere and press the shortcut to read aloud. Press again to stop.")
        )
        shortcut_row.set_icon_name("preferences-desktop-keyboard-shortcuts-symbolic")

        # Shortcut label showing current keybinding
        self._shortcut_label = Gtk.ShortcutLabel()
        self._shortcut_label.set_accelerator(self._settings.shortcut.keybinding)
        self._shortcut_label.set_valign(Gtk.Align.CENTER)

        # "Change" button to start recording
        self._shortcut_button = Gtk.Button(label=_("Change"))
        self._shortcut_button.set_valign(Gtk.Align.CENTER)
        self._shortcut_button.add_css_class("flat")
        self._shortcut_button.connect("clicked", self._on_shortcut_change_clicked)

        shortcut_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        shortcut_box.append(self._shortcut_label)
        shortcut_box.append(self._shortcut_button)
        shortcut_row.add_suffix(shortcut_box)

        expander.add_row(shortcut_row)

        # ── Show speak action in app launcher ──
        launcher_row, self._launcher_switch_widget = create_action_row_with_switch(
            title=_("Show speak button in app launcher"),
            subtitle=_("Adds a launcher entry you can pin to the taskbar"),
            active=self._settings.shortcut.show_in_launcher,
            on_toggled=self._on_launcher_toggle,
        )
        launcher_row.set_icon_name("view-pin-symbolic")
        expander.add_row(launcher_row)

        group.add(expander)
        return group

    def _on_shortcut_change_clicked(self, button: Gtk.Button) -> None:
        """Open a small key-capture window for shortcut recording."""
        parent = self.get_root()

        # Create a capture window
        win = Adw.Window()
        win.set_title(_("Set new shortcut"))
        win.set_default_size(500, 320)
        win.set_modal(True)
        if parent:
            win.set_transient_for(parent)
        win.set_resizable(False)

        # Simple layout with a status page
        status = Adw.StatusPage()
        status.set_icon_name("input-keyboard-symbolic")
        status.set_title(_("Press the new shortcut"))
        status.set_description(
            _("Hold modifier keys (Alt, Ctrl, Shift, Super)\nand press a letter or key.\n\nPress Escape to cancel.")
        )
        # Reduce icon size
        status.set_icon_name("")
        status.set_title(_("Press the new shortcut"))

        # Custom smaller icon
        icon = Gtk.Image.new_from_icon_name("input-keyboard-symbolic")
        icon.set_pixel_size(48)
        icon.set_margin_top(24)
        icon.add_css_class("dim-label")

        desc_label = Gtk.Label()
        desc_label.set_markup(
            _("<b>Hold modifier keys</b> (Alt, Ctrl, Shift, Super)\n"
              "and press a letter or key.\n\n"
              "<small>Press Escape to cancel</small>")
        )
        desc_label.set_justify(Gtk.Justification.CENTER)
        desc_label.set_margin_top(16)
        desc_label.set_margin_bottom(16)

        title_label = Gtk.Label(label=_("Press the new shortcut"))
        title_label.add_css_class("title-3")
        title_label.set_margin_top(12)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.set_valign(Gtk.Align.CENTER)
        content_box.set_halign(Gtk.Align.CENTER)
        content_box.append(icon)
        content_box.append(title_label)
        content_box.append(desc_label)

        win.set_content(content_box)

        # Key controller on the capture window
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_capture_key_pressed, win)
        win.add_controller(key_ctrl)

        win.present()

    def _on_capture_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: int,
        win: Adw.Window,
    ) -> bool:
        """Handle key press in the capture window."""
        from gi.repository import Gdk

        # Ignore modifier-only presses
        modifier_keys = {
            Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
            Gdk.KEY_Control_L, Gdk.KEY_Control_R,
            Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
            Gdk.KEY_Super_L, Gdk.KEY_Super_R,
            Gdk.KEY_Meta_L, Gdk.KEY_Meta_R,
        }
        if keyval in modifier_keys:
            return False

        # Escape = cancel
        if keyval == Gdk.KEY_Escape and not (
            state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK)
        ):
            win.close()
            return True

        # Build accelerator string from modifiers + key
        mods = state & (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.SHIFT_MASK
            | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.SUPER_MASK
        )

        accel = Gtk.accelerator_name(keyval, mods)
        if not accel:
            return False

        # Save the new shortcut
        self._settings.shortcut.keybinding = accel
        self._settings_service.save(self._settings)

        # Update UI
        self._shortcut_label.set_accelerator(accel)

        # Update KDE shortcut files
        self._update_khotkeys(accel)

        win.close()

        display_name = Gtk.accelerator_get_label(keyval, mods)
        self._on_toast(
            _("Shortcut changed to {keys}").format(keys=display_name), 3
        )
        logger.info("Shortcut changed to: %s (%s)", accel, display_name)
        return True

    def _update_khotkeys(self, accel: str) -> None:
        """Update the KDE shortcut with the new keybinding.

        On Plasma 6, shortcuts are set via X-KDE-Shortcuts in .desktop files
        and kglobalshortcutsrc. We update both the user-local desktop file
        override and the kglobalshortcutsrc.
        """
        from pathlib import Path
        import re

        # Convert GTK accelerator to KDE format
        # GTK: <Alt>v  →  KDE: Alt+V
        # GTK: <Control><Shift>s  →  KDE: Ctrl+Shift+S
        kde_shortcut = accel
        kde_shortcut = kde_shortcut.replace("<Control>", "Ctrl+")
        kde_shortcut = kde_shortcut.replace("<Shift>", "Shift+")
        kde_shortcut = kde_shortcut.replace("<Alt>", "Alt+")
        kde_shortcut = kde_shortcut.replace("<Super>", "Meta+")
        # Capitalize the final key
        if "+" in kde_shortcut:
            parts = kde_shortcut.rsplit("+", 1)
            kde_shortcut = parts[0] + "+" + parts[1].upper()
        else:
            kde_shortcut = kde_shortcut.upper()

        logger.info("Updating KDE shortcut to: %s", kde_shortcut)

        # 1. Create user-local .desktop override with new shortcut
        local_apps = Path.home() / ".local" / "share" / "applications"
        local_apps.mkdir(parents=True, exist_ok=True)
        desktop_src = Path("/usr/share/applications/bigtts.desktop")
        desktop_dst = local_apps / "bigtts.desktop"

        try:
            if desktop_src.exists():
                content = desktop_src.read_text()
            elif desktop_dst.exists():
                content = desktop_dst.read_text()
            else:
                logger.warning("bigtts.desktop not found")
                return

            content = re.sub(
                r"^X-KDE-Shortcuts=.*$",
                f"X-KDE-Shortcuts={kde_shortcut}",
                content,
                flags=re.MULTILINE,
            )
            desktop_dst.write_text(content)
            logger.info("Updated desktop file: %s", desktop_dst)
        except OSError as e:
            logger.warning("Could not update desktop file: %s", e)

        # 2. Update kglobalshortcutsrc via kwriteconfig6
        try:
            subprocess.run(
                [
                    "kwriteconfig6",
                    "--file", "kglobalshortcutsrc",
                    "--group", "bigtts.desktop",
                    "--key", "_launch",
                    f"{kde_shortcut}\t,Alt+V\t,Speech or stop selected text",
                ],
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("Could not update kglobalshortcutsrc: %s", e)

        # 3. Notify KGlobalAccel to reload
        try:
            subprocess.run(
                ["dbus-send", "--type=signal", "--session",
                 "/KGlobalSettings", "org.kde.KGlobalSettings.notifyChange",
                 "int32:3", "int32:0"],
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    # ── Launcher toggle ────────────────────────────────────────────

    def _on_launcher_toggle(self, active: bool) -> None:
        """Toggle visibility of the speak action in the app launcher."""
        from pathlib import Path
        import re

        if self._updating_ui:
            return

        self._settings.shortcut.show_in_launcher = active
        self._settings_service.save()

        local_apps = Path.home() / ".local" / "share" / "applications"
        local_apps.mkdir(parents=True, exist_ok=True)
        desktop_dst = local_apps / "bigtts.desktop"

        # Ensure icon is available (handle old→new name transition)
        self._ensure_icon_available()

        if active:
            desktop_src = Path("/usr/share/applications/bigtts.desktop")
            try:
                if desktop_src.exists():
                    content = desktop_src.read_text()
                elif desktop_dst.exists():
                    content = desktop_dst.read_text()
                else:
                    accel = self._settings.shortcut.keybinding
                    kde_key = accel.replace("<Control>", "Ctrl+").replace("<Shift>", "Shift+").replace("<Alt>", "Alt+").replace("<Super>", "Meta+")
                    if "+" in kde_key:
                        parts = kde_key.rsplit("+", 1)
                        kde_key = parts[0] + "+" + parts[1].upper()
                    content = (
                        "[Desktop Entry]\n"
                        "Type=Application\n"
                        "Exec=biglinux-tts-speak\n"
                        "Icon=biglinux-tts\n"
                        "Categories=Utility;Accessibility;\n"
                        "StartupNotify=false\n"
                        f"X-KDE-Shortcuts={kde_key}\n"
                        "Name=Speech or stop selected text\n"
                        "Name[pt_BR]=Narrador de texto\n"
                    )
                # Remove NoDisplay and any Desktop Actions junk
                content = re.sub(r"^NoDisplay=.*\n?", "", content, flags=re.MULTILINE)
                content = re.sub(r"\nActions=.*(?:\n\[Desktop Action [^\]]+\].*)*$", "", content, flags=re.DOTALL)
                content = re.sub(r"^Exec=.*$", "Exec=biglinux-tts-speak", content, flags=re.MULTILINE)
                content = re.sub(r"^Icon=.*$", "Icon=biglinux-tts", content, flags=re.MULTILINE)
                desktop_dst.write_text(content)
            except OSError as e:
                logger.warning("Could not create launcher entry: %s", e)

            self._update_desktop_database()
            self._on_toast(_("Added to app launcher — may need re-login to appear in panel"), 4)
        else:
            try:
                if desktop_dst.exists():
                    content = desktop_dst.read_text()
                    if "NoDisplay" not in content:
                        content = content.replace(
                            "StartupNotify=false",
                            "StartupNotify=false\nNoDisplay=true",
                        )
                        desktop_dst.write_text(content)
            except OSError as e:
                logger.warning("Could not hide launcher entry: %s", e)

            self._update_desktop_database()

    @staticmethod
    def _ensure_icon_available() -> None:
        """Ensure the biglinux-tts icon is resolvable.

        Handles the transition from old icon name (tts-biglinux) to new
        (biglinux-tts) by creating a local symlink if the system only
        has the old icon installed.
        """
        from pathlib import Path

        icon_dirs = [
            Path("/usr/share/icons/hicolor/scalable/apps"),
        ]
        new_name = "biglinux-tts.svg"
        old_name = "tts-biglinux.svg"
        local_icon_dir = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"

        # Check if the new icon already exists anywhere
        for d in icon_dirs:
            if (d / new_name).exists():
                return

        # Check if local icon already set up
        if (local_icon_dir / new_name).exists():
            return

        # Check if old icon exists and create a symlink with new name
        for d in icon_dirs:
            old_path = d / old_name
            if old_path.exists():
                local_icon_dir.mkdir(parents=True, exist_ok=True)
                try:
                    (local_icon_dir / new_name).symlink_to(old_path)
                    logger.info("Created icon symlink: %s -> %s", local_icon_dir / new_name, old_path)
                except OSError as e:
                    logger.warning("Could not create icon symlink: %s", e)
                return

    @staticmethod
    def _update_desktop_database() -> None:
        """Update the desktop file database so KDE picks up changes."""
        from pathlib import Path

        local_apps = Path.home() / ".local" / "share" / "applications"
        try:
            subprocess.run(
                ["update-desktop-database", str(local_apps)],
                timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        # Rebuild KDE service cache to make changes visible
        try:
            subprocess.run(
                ["kbuildsycoca6", "--noincremental"],
                timeout=10, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    # ── Voice Discovery Callback ─────────────────────────────────────

    def _on_voices_discovered(self, catalog: VoiceCatalog) -> None:
        """Handle voice discovery completion (called on main thread)."""
        self._catalog = catalog

        if not catalog.voices:
            self._voice_combo.set_subtitle(_("No voices found — install rhvoice or espeak-ng"))
            self._voice_combo.set_model(
                Gtk.StringList.new([_("No voices available")])
            )
            self._voice_list = []
            self._backend_status.set_subtitle(_("No TTS voices installed"))
            return

        # Filter voices by selected backend
        current_backend = self._settings.speech.backend
        filtered = catalog.get_by_backend(current_backend)

        if not filtered:
            # No voices for selected backend
            self._voice_list = []
            self._updating_ui = True
            self._voice_combo.set_model(
                Gtk.StringList.new([_("No voices available for this engine")])
            )
            self._voice_combo.set_subtitle(
                _("Install {engine} voices first").format(engine=current_backend)
            )
            self._test_button.set_sensitive(False)
            self._updating_ui = False
            return

        self._voice_list = filtered
        self._test_button.set_sensitive(True)

        # Build display names
        display_names: list[str] = []
        for v in self._voice_list:
            quality_tag = ""
            if v.quality == "neural":
                quality_tag = " [Neural]"
            elif v.quality == "high":
                quality_tag = " [HQ]"

            display_names.append(
                f"{v.name} — {v.language_name}{quality_tag}"
            )

        # Update combo
        self._updating_ui = True
        model = Gtk.StringList.new(display_names)
        self._voice_combo.set_model(model)
        self._voice_combo.set_subtitle(
            _("{count} voices available").format(count=len(display_names))
        )

        # Select current voice
        current_voice_id = self._settings.speech.voice_id
        selected_idx = 0

        if current_voice_id:
            for i, v in enumerate(self._voice_list):
                if v.voice_id == current_voice_id:
                    selected_idx = i
                    break

        # If current voice not in filtered list, auto-select best
        if selected_idx == 0 and current_voice_id:
            # Voice not found in current backend — pick best for language
            sys_lang = get_system_language()
            for i, v in enumerate(self._voice_list):
                if v.language.startswith(sys_lang):
                    selected_idx = i
                    break

        self._voice_combo.set_selected(selected_idx)
        self._updating_ui = False

        # Update settings with selected voice
        if self._voice_list:
            voice = self._voice_list[selected_idx]
            self._settings.speech.voice_id = voice.voice_id
            self._settings.speech.backend = voice.backend
            self._settings.speech.output_module = voice.output_module
            self._settings_service.save(self._settings)

        logger.info(
            "Voice catalog loaded: %d voices (filtered from %d)",
            len(self._voice_list),
            len(catalog.voices),
        )

    # ── Event Handlers ───────────────────────────────────────────────

    def _on_voice_selected(self, index: int) -> None:
        """Handle voice selection change."""
        if self._updating_ui or not self._voice_list:
            return
        if 0 <= index < len(self._voice_list):
            voice = self._voice_list[index]
            self._settings.speech.voice_id = voice.voice_id
            self._settings.speech.backend = voice.backend
            self._settings.speech.output_module = voice.output_module
            self._settings_service.save(self._settings)
            logger.debug("Voice selected: %s (%s)", voice.name, voice.voice_id)

    def _on_rate_changed(self, value: float) -> None:
        """Handle speed change."""
        self._settings.speech.rate = int(value)
        self._settings_service.save(self._settings)

    def _on_pitch_changed(self, value: float) -> None:
        """Handle pitch change."""
        self._settings.speech.pitch = int(value)
        self._settings_service.save(self._settings)

    def _on_volume_changed(self, value: float) -> None:
        """Handle volume change."""
        self._settings.speech.volume = int(value)
        self._settings_service.save(self._settings)

    def _on_backend_selected(self, index: int) -> None:
        """Handle backend selection."""
        if self._updating_ui:
            return
        backend_map = {
            0: TTSBackend.SPEECH_DISPATCHER.value,
            1: TTSBackend.ESPEAK_NG.value,
            2: TTSBackend.PIPER.value,
        }
        backend = backend_map.get(index, TTSBackend.SPEECH_DISPATCHER.value)
        logger.debug("Backend selected: index=%d → %s", index, backend)
        self._settings.speech.backend = backend
        self._settings_service.save(self._settings)

        # Update subtitle
        names = {
            TTSBackend.SPEECH_DISPATCHER.value: "speech-dispatcher",
            TTSBackend.ESPEAK_NG.value: "espeak-ng",
            TTSBackend.PIPER.value: "Piper",
        }
        self._backend_expander.set_subtitle(names.get(backend, backend))

        # Show/hide output module based on backend
        self._module_combo.set_visible(backend == TTSBackend.SPEECH_DISPATCHER.value)

        # Refresh voices for new backend
        if self._catalog:
            filtered = self._catalog.get_by_backend(backend)
            if not filtered and backend == TTSBackend.PIPER.value:
                self._ask_install_piper()
                return
            elif not filtered:
                self._on_toast(
                    _("No voices found for {engine} — install it first").format(
                        engine=names.get(backend, backend)
                    ),
                    4,
                )
            self._on_voices_discovered(self._catalog)

    def _ask_install_piper(self) -> None:
        """Show dialog to install Piper TTS and voices."""
        dialog = Adw.AlertDialog.new(
            _("Install Piper Neural TTS?"),
            _(
                "Piper is not installed. It provides high-quality neural "
                "voices for reading text.\n\n"
                "The following packages will be installed:\n"
                "• piper-tts-bin (TTS engine)\n"
                "• piper-voices-pt-BR (Portuguese voices)\n\n"
                "This requires administrator permissions."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("install", _("Install"))
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_install_piper_response)

        window = self.get_root()
        dialog.present(window)

    def _on_install_piper_response(
        self, dialog: Adw.AlertDialog, response: str
    ) -> None:
        """Handle install dialog response."""
        if response != "install":
            # Revert to previous backend
            self._updating_ui = True
            prev_backend = TTSBackend.SPEECH_DISPATCHER.value
            self._settings.speech.backend = prev_backend
            self._settings_service.save(self._settings)
            self._backend_combo.set_selected(0)
            self._backend_expander.set_subtitle("speech-dispatcher")
            self._module_combo.set_visible(True)
            if self._catalog:
                self._on_voices_discovered(self._catalog)
            self._updating_ui = False
            return

        self._on_toast(_("Installing Piper TTS — this may take a moment…"), 5)
        run_in_thread(self._install_piper_packages, on_done=self._on_piper_installed)

    def _install_piper_packages(self) -> bool:
        """Install piper-tts-bin and piper-voices-pt-BR via pkexec + pacman."""
        # Detect system language for voice package
        lang = get_system_language()
        voice_pkgs = []
        lang_map = {
            "pt": "piper-voices-pt-BR",
            "en": "piper-voices-en-US",
            "es": "piper-voices-es-ES",
            "fr": "piper-voices-fr-FR",
            "de": "piper-voices-de-DE",
            "it": "piper-voices-it-IT",
            "ru": "piper-voices-ru-RU",
            "ja": "piper-voices-ja-JP",
            "ko": "piper-voices-ko-KR",
            "zh": "piper-voices-zh-CN",
        }
        voice_pkg = lang_map.get(lang, "piper-voices-en-US")
        voice_pkgs.append(voice_pkg)

        # Always include pt-BR for BigLinux
        if voice_pkg != "piper-voices-pt-BR":
            voice_pkgs.append("piper-voices-pt-BR")

        pkgs = ["piper-tts-bin"] + voice_pkgs

        try:
            result = subprocess.run(
                ["pkexec", "pacman", "-S", "--noconfirm", "--needed"] + pkgs,
                capture_output=True,
                text=True,
                timeout=300,
            )
            logger.info("Piper install stdout: %s", result.stdout[-200:] if result.stdout else "")
            if result.returncode != 0:
                logger.error("Piper install failed: %s", result.stderr[-300:] if result.stderr else "")
                return False
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.error("Failed to install Piper: %s", e)
            return False

    def _on_piper_installed(self, success: bool) -> None:
        """Called after Piper install completes."""
        if success:
            self._on_toast(_("Piper installed successfully! Discovering voices…"), 3)
            # Re-discover voices
            run_in_thread(discover_voices, on_done=self._on_voices_discovered)
        else:
            self._on_toast(_("Failed to install Piper — check permissions"), 5)
            # Revert to speech-dispatcher
            self._updating_ui = True
            self._settings.speech.backend = TTSBackend.SPEECH_DISPATCHER.value
            self._settings_service.save(self._settings)
            self._backend_combo.set_selected(0)
            self._backend_expander.set_subtitle("speech-dispatcher")
            self._module_combo.set_visible(True)
            if self._catalog:
                self._on_voices_discovered(self._catalog)
            self._updating_ui = False

    def _on_module_selected(self, index: int) -> None:
        """Handle output module selection."""
        modules = ["rhvoice", "espeak-ng", "espeak", "pico", "festival"]
        if 0 <= index < len(modules):
            self._settings.speech.output_module = modules[index]
            self._settings_service.save(self._settings)

    def _on_abbreviations_toggled(self, active: bool) -> None:
        self._settings.text.expand_abbreviations = active
        self._settings_service.save(self._settings)

    def _on_special_chars_toggled(self, active: bool) -> None:
        self._settings.text.process_special_chars = active
        self._settings_service.save(self._settings)

    def _on_strip_formatting_toggled(self, active: bool) -> None:
        self._settings.text.strip_formatting = active
        self._settings_service.save(self._settings)

    def _on_urls_toggled(self, active: bool) -> None:
        self._settings.text.process_urls = active
        self._settings_service.save(self._settings)

    def _on_max_chars_changed(self, value: float) -> None:
        self._settings.text.max_chars = int(value)
        self._settings_service.save(self._settings)

    def _on_max_chars_selected(self, combo: Adw.ComboRow, _pspec: object) -> None:
        """Handle character limit combo selection."""
        if self._updating_ui:
            return
        idx = combo.get_selected()
        if 0 <= idx < len(self._char_limit_values):
            self._settings.text.max_chars = self._char_limit_values[idx]
            self._settings_service.save(self._settings)

    # ── Test Voice ───────────────────────────────────────────────────

    def _on_test_voice(self) -> None:
        """Test the selected voice / stop if already speaking."""
        # Toggle: if speaking, stop
        if self._tts.is_speaking:
            self._tts.stop()
            return

        # Read text from the test entry field
        phrase = self._test_entry.get_text().strip()
        if not phrase:
            self._on_toast(_("Type some text to test"), 2)
            return

        # Get selected voice
        voice = None
        idx = self._voice_combo.get_selected()
        if self._voice_list and 0 <= idx < len(self._voice_list):
            voice = self._voice_list[idx]

        settings = self._settings.speech

        # Warn if volume is at zero
        test_volume = settings.volume
        if test_volume <= 0:
            self._on_toast(_("Volume is at zero — increasing to minimum for test"), 3)
            test_volume = 10

        logger.info(
            "Test voice: backend=%s, voice_id=%s, volume=%d, text=%r",
            voice.backend if voice else settings.backend,
            voice.voice_id if voice else settings.voice_id,
            test_volume,
            phrase[:60],
        )

        success = self._tts.speak(
            phrase,
            voice=voice,
            rate=settings.rate,
            pitch=settings.pitch,
            volume=test_volume,
            backend=settings.backend,
            output_module=settings.output_module,
            voice_id=settings.voice_id,
            expand_abbreviations=False,
            process_special_chars=False,
        )

        if not success:
            self._on_toast(_("Could not play test — check if a TTS engine is installed"), 4)

    # ── TTS State Callback ───────────────────────────────────────────

    def _on_tts_state_changed(self, state: TTSState) -> None:
        """Update hero section when TTS state changes."""
        GLib.idle_add(self._update_hero_state, state)

    def _update_hero_state(self, state: TTSState) -> bool:
        """Update hero UI for current TTS state (main thread)."""
        if state == TTSState.SPEAKING:
            self._hero_icon.set_from_icon_name("audio-volume-high-symbolic")
            self._hero_icon.add_css_class("speaking-indicator")
            self._hero_title.set_markup(f"<b>{_('Speaking...')}</b>")
            self._hero_subtitle.set_label(_("Press Alt+V to stop"))
            self._test_button.set_label(_("Stop"))
            self._test_button.remove_css_class("suggested-action")
            self._test_button.add_css_class("destructive-action")

        elif state == TTSState.ERROR:
            self._hero_icon.set_from_icon_name("dialog-warning-symbolic")
            self._hero_icon.remove_css_class("speaking-indicator")
            self._hero_title.set_markup(f"<b>{_('Error')}</b>")
            self._hero_subtitle.set_label(_("Could not play speech — check TTS engine"))
            self._test_button.set_label(_("Test voice"))
            self._test_button.remove_css_class("destructive-action")
            self._test_button.add_css_class("suggested-action")

        else:  # IDLE
            self._hero_icon.set_from_icon_name("audio-speakers-symbolic")
            self._hero_icon.remove_css_class("speaking-indicator")
            self._hero_title.set_markup(f"<b>{_('Ready to speak')}</b>")
            self._hero_subtitle.set_label(
                _("Select text and press Alt+V to read aloud")
            )
            self._test_button.set_label(_("Test voice"))
            self._test_button.remove_css_class("destructive-action")
            self._test_button.add_css_class("suggested-action")

        return GLib.SOURCE_REMOVE

    # ── Backend Status Check ─────────────────────────────────────────

    def _check_backend_status(self) -> bool:
        """Check if TTS backends are available."""
        import shutil

        available = []
        if shutil.which("spd-say"):
            available.append("speech-dispatcher")
        if shutil.which("espeak-ng"):
            available.append("espeak-ng")
        if shutil.which("piper"):
            available.append("Piper")

        if available:
            self._backend_status.set_subtitle(
                _("Available: {engines}").format(engines=", ".join(available))
            )
        else:
            self._backend_status.set_subtitle(
                _("No TTS engine found — install speech-dispatcher or espeak-ng")
            )

        return GLib.SOURCE_REMOVE

    # ── Restore Defaults ─────────────────────────────────────────────

    def restore_defaults(self) -> None:
        """Reset all settings to defaults and update UI."""
        self._settings = self._settings_service.reset_to_defaults()
        self._update_ui_from_settings()
        self._on_toast(_("Settings restored to defaults"), 3)

    def _update_ui_from_settings(self) -> None:
        """Sync all UI widgets with current settings."""
        self._updating_ui = True

        # Voice settings sliders
        self._speed_scale.set_value(self._settings.speech.rate)
        self._pitch_scale.set_value(self._settings.speech.pitch)
        self._volume_scale.set_value(self._settings.speech.volume)

        # Text processing switches
        self._abbr_switch.set_active(self._settings.text.expand_abbreviations)
        self._chars_switch.set_active(self._settings.text.process_special_chars)
        self._fmt_switch.set_active(self._settings.text.strip_formatting)
        self._url_switch.set_active(self._settings.text.process_urls)

        # Character limit combo
        current_limit = self._settings.text.max_chars
        for i, val in enumerate(self._char_limit_values):
            if val == current_limit:
                self._max_chars_combo.set_selected(i)
                break

        # Backend combo
        backend_map = {
            TTSBackend.SPEECH_DISPATCHER.value: 0,
            TTSBackend.ESPEAK_NG.value: 1,
            TTSBackend.PIPER.value: 2,
        }
        backend_idx = backend_map.get(self._settings.speech.backend, 0)
        self._backend_combo.set_selected(backend_idx)

        # Output module combo
        modules = ["rhvoice", "espeak-ng", "espeak", "pico", "festival"]
        mod_idx = 0
        for i, m in enumerate(modules):
            if m == self._settings.speech.output_module:
                mod_idx = i
                break
        self._module_combo.set_selected(mod_idx)

        # Keyboard shortcut
        self._shortcut_label.set_accelerator(self._settings.shortcut.keybinding)

        # Launcher toggle
        self._launcher_switch_widget.set_active(self._settings.shortcut.show_in_launcher)

        # Update KDE shortcut to default
        self._update_khotkeys(self._settings.shortcut.keybinding)

        self._updating_ui = False
