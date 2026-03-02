"""
Welcome window for BigLinux TTS.

Standalone Gtk.Window presenting the main features on first launch.
Completely independent from the main application window.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from utils.i18n import _

if TYPE_CHECKING:
    from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


class WelcomeWindow(Adw.Window):
    """Standalone welcome window explaining TTS BigLinux features."""

    def __init__(
        self,
        application: Gtk.Application,
        settings_service: SettingsService,
    ) -> None:
        super().__init__(
            title=_("Welcome to BigLinux TTS"),
            default_width=590,
            default_height=700,
            resizable=True,
            deletable=True,
        )
        # Bind to application so it keeps the app alive
        self.set_application(application)
        self._settings_service = settings_service
        self._show_switch: Gtk.Switch | None = None

        self._build_ui()

    # ── Public ────────────────────────────────────────────────────

    @staticmethod
    def should_show(settings_service: SettingsService) -> bool:
        """Return True if the welcome window should be shown."""
        return settings_service.get().show_welcome

    # ── UI Construction ───────────────────────────────────────────

    def _build_ui(self) -> None:
        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        toolbar.add_top_bar(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.set_margin_top(24)
        content.set_margin_bottom(24)

        # Header
        content.append(self._build_header())

        # Feature columns
        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        columns.set_margin_top(18)
        columns.set_halign(Gtk.Align.CENTER)
        columns.set_hexpand(True)

        columns.append(self._build_left_column())
        columns.append(self._build_right_column())
        content.append(columns)

        # Separator + switch
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(12)
        content.append(sep)
        content.append(self._build_switch_row())

        # Close button
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_box.set_margin_top(18)
        btn_box.set_halign(Gtk.Align.CENTER)

        btn = Gtk.Button(label=_("Let's Start"))
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_size_request(150, -1)
        btn.connect("clicked", self._on_close_clicked)
        btn_box.append(btn)
        content.append(btn_box)

        scrolled.set_child(content)
        toolbar.set_content(scrolled)
        self.set_content(toolbar)

    def _build_header(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_halign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name("tts-biglinux")
        icon.set_pixel_size(64)
        box.append(icon)

        title = Gtk.Label()
        title.set_markup(
            "<span size='xx-large' weight='bold'>"
            + _("Welcome to BigLinux TTS")
            + "</span>"
        )
        box.append(title)

        subtitle = Gtk.Label()
        subtitle.set_markup(
            "<span size='large'>"
            + _("Your text-to-speech assistant")
            + "</span>"
        )
        subtitle.add_css_class("dim-label")
        box.append(subtitle)

        return box

    def _build_left_column(self) -> Gtk.Widget:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        col.set_hexpand(True)

        features = [
            (
                "🗣️ " + _("Multiple TTS Engines"),
                _(
                    "RHVoice, espeak-ng and Piper Neural TTS\n"
                    "with automatic voice discovery"
                ),
            ),
            (
                "🌍 " + _("Multilingual Support"),
                _(
                    "Voices in dozens of languages\n"
                    "including Portuguese (Brazil)"
                ),
            ),
            (
                "⌨️ " + _("Global Shortcut"),
                _(
                    "Press Alt+V to read selected text\n"
                    "from any application"
                ),
            ),
            (
                "📋 " + _("Clipboard Reading"),
                _(
                    "Paste or type text directly\n"
                    "and listen instantly"
                ),
            ),
        ]
        for title, desc in features:
            col.append(self._make_feature(title, desc))
        return col

    def _build_right_column(self) -> Gtk.Widget:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        col.set_hexpand(True)

        features = [
            (
                "🎛️ " + _("Fine-Tune Speech"),
                _(
                    "Adjust speed, pitch and volume\n"
                    "to your preference"
                ),
            ),
            (
                "🤖 " + _("Neural Voices"),
                _(
                    "Install Piper voices for natural,\n"
                    "high-quality speech synthesis"
                ),
            ),
            (
                "📦 " + _("Easy Installation"),
                _(
                    "Install new voices and engines\n"
                    "directly from the interface"
                ),
            ),
            (
                "⚡ " + _("Lightweight & Fast"),
                _(
                    "Native GTK4/Adwaita interface\n"
                    "with minimal resource usage"
                ),
            ),
        ]
        for title, desc in features:
            col.append(self._make_feature(title, desc))
        return col

    def _build_switch_row(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(12)

        label = Gtk.Label(label=_("Show this dialog on startup"))
        label.set_xalign(0)
        label.set_hexpand(True)

        self._show_switch = Gtk.Switch()
        self._show_switch.set_valign(Gtk.Align.CENTER)
        self._show_switch.set_active(
            self._settings_service.get().show_welcome
        )

        box.append(label)
        box.append(self._show_switch)
        return box

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _make_feature(title: str, description: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        title_lbl = Gtk.Label()
        title_lbl.set_markup(GLib.markup_escape_text(title))
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_wrap(True)
        box.append(title_lbl)

        desc_lbl = Gtk.Label(label=description)
        desc_lbl.set_halign(Gtk.Align.START)
        desc_lbl.set_wrap(True)
        desc_lbl.set_xalign(0)
        desc_lbl.add_css_class("dim-label")
        desc_lbl.set_max_width_chars(40)
        box.append(desc_lbl)

        return box

    # ── Callbacks ─────────────────────────────────────────────────

    def _on_close_clicked(self, _button: Gtk.Button) -> None:
        if self._show_switch is not None:
            settings = self._settings_service.get()
            settings.show_welcome = self._show_switch.get_active()
            self._settings_service.save(settings)

        self.close()
