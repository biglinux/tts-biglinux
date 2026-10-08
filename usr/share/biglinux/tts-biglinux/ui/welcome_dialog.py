"""First-run welcome dialog for BigLinux TTS."""

# ruff: noqa: E402  # gi.require_version must run before repository imports.

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from utils.i18n import _

if TYPE_CHECKING:
    from services.settings_service import SettingsService


class WelcomeDialog(Adw.Dialog):
    """Welcome dialog explaining BigLinux TTS capabilities."""

    def __init__(
        self,
        application: Gtk.Application,
        settings_service: SettingsService,
    ) -> None:
        super().__init__()
        self._application = application
        self._settings_service = settings_service
        self._show_switch: Gtk.Switch | None = None
        self.connect("closed", self._on_closed)
        self._build_ui()

    @staticmethod
    def should_show(settings_service: SettingsService) -> bool:
        """Return whether the introduction should be shown at startup."""
        return settings_service.get().show_welcome

    def _build_ui(self) -> None:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(20)
        content.set_margin_bottom(12)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        header.set_halign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name("tts-biglinux")
        icon.set_pixel_size(64)
        header.append(icon)

        title = Gtk.Label()
        welcome = _("Welcome to BigLinux TTS")
        title.set_markup(
            f"<span size='xx-large' weight='bold'>{GLib.markup_escape_text(welcome)}</span>"
        )
        header.append(title)

        subtitle = Gtk.Label()
        tagline = _("Your text-to-speech assistant")
        subtitle.set_markup(f"<span size='large'>{GLib.markup_escape_text(tagline)}</span>")
        subtitle.add_css_class("dim-label")
        header.append(subtitle)

        content.append(header)

        features = [
            (
                "audio-input-microphone-symbolic",
                _("Multiple TTS Engines"),
                _(
                    "RHVoice, espeak-ng, Piper and Kokoro\n"
                    "with automatic voice discovery"
                ),
                "preferences-desktop-locale-symbolic",
                _("Multilingual Support"),
                _("Voices in dozens of languages\nincluding Portuguese (Brazil)"),
            ),
            (
                "input-keyboard-symbolic",
                _("Global Shortcut"),
                _("Press {key} to read selected text\nfrom any application").format(
                    key=self._shortcut_display()
                ),
                "edit-paste-symbolic",
                _("Clipboard Reading"),
                _("Paste or type text directly\nand listen instantly"),
            ),
            (
                "preferences-system-symbolic",
                _("Fine-Tune Speech"),
                _("Adjust speed, pitch and volume\nto your preference"),
                "folder-download-symbolic",
                _("Easy Installation"),
                _("Install new voices and engines\ndirectly from the interface"),
            ),
            (
                "audio-speakers-symbolic",
                _("Neural Voices"),
                _(
                    "Install Piper or Kokoro voices for natural,\n"
                    "high-quality speech synthesis"
                ),
                "document-open-recent-symbolic",
                _("Reading History"),
                _(
                    "Optionally save and replay previous\n"
                    "readings when history is enabled"
                ),
            ),
        ]

        grid = Gtk.Grid()
        grid.set_row_spacing(16)
        grid.set_column_spacing(24)
        grid.set_margin_top(18)
        grid.set_halign(Gtk.Align.CENTER)
        grid.set_hexpand(True)

        for row_idx, (
            left_icon,
            left_title,
            left_desc,
            right_icon,
            right_title,
            right_desc,
        ) in enumerate(features):
            left_box = self._create_feature_box(left_icon, left_title, left_desc)
            left_box.set_hexpand(True)
            grid.attach(left_box, 0, row_idx, 1, 1)

            right_box = self._create_feature_box(right_icon, right_title, right_desc)
            right_box.set_hexpand(True)
            grid.attach(right_box, 1, row_idx, 1, 1)

        content.append(grid)

        shortcuts_label = Gtk.Label()
        tip = _("Tip: Press {key} to read selected text from any application.").format(
            key=self._shortcut_display()
        )
        shortcuts_label.set_markup(f"<span size='small'>{GLib.markup_escape_text(tip)}</span>")
        shortcuts_label.add_css_class("dim-label")
        shortcuts_label.set_margin_top(12)
        content.append(shortcuts_label)

        scrolled.set_child(content)

        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bottom_bar.set_margin_start(20)
        bottom_bar.set_margin_end(20)
        bottom_bar.set_margin_top(12)
        bottom_bar.set_margin_bottom(16)

        self._show_switch = Gtk.Switch()
        self._show_switch.set_valign(Gtk.Align.CENTER)
        self._show_switch.set_active(self._settings_service.get().show_welcome)
        self._show_switch.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Show dialog on startup")]
        )

        switch_label = Gtk.Label(label=_("Show dialog on startup"))
        switch_label.set_xalign(0)

        switch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        switch_box.append(self._show_switch)
        switch_box.append(switch_label)
        switch_box.set_hexpand(True)
        bottom_bar.append(switch_box)

        start_button = Gtk.Button(label=_("Let's Start"))
        start_button.add_css_class("suggested-action")
        start_button.add_css_class("pill")
        start_button.set_size_request(150, -1)
        start_button.connect("clicked", self._on_close)
        bottom_bar.append(start_button)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(scrolled)
        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        outer.append(bottom_bar)

        handle = Gtk.WindowHandle()
        handle.set_child(outer)

        self.set_content_width(900)
        self.set_content_height(650)
        self.set_child(handle)

    def _shortcut_display(self) -> str:
        """Return the configured shortcut in a compact, human-readable form."""
        from services.shortcut_service import display_text

        return display_text(self._settings_service.get().shortcut.keybinding)

    @staticmethod
    def _create_feature_box(
        icon_name: str,
        title: str,
        description: str,
    ) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(32)
        icon.set_valign(Gtk.Align.START)
        icon.add_css_class("dim-label")
        row.append(icon)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        title_label = Gtk.Label()
        title_label.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        title_label.set_halign(Gtk.Align.START)
        title_label.set_wrap(True)
        text_box.append(title_label)

        description_label = Gtk.Label(label=description)
        description_label.set_halign(Gtk.Align.START)
        description_label.set_wrap(True)
        description_label.set_xalign(0)
        description_label.add_css_class("dim-label")
        description_label.set_max_width_chars(40)
        text_box.append(description_label)

        row.append(text_box)
        return row

    def _save_preferences(self) -> None:
        if self._show_switch is None:
            return
        settings = self._settings_service.get()
        settings.show_welcome = self._show_switch.get_active()
        self._settings_service.save(settings)

    def _on_close(self, _button: Gtk.Button) -> None:
        self._save_preferences()
        self.close()

    def _on_closed(self, _dialog: Adw.Dialog) -> None:
        self._save_preferences()
