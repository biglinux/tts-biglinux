"""
History view — grid/list of past TTS entries with inline audio player.

Reads history.json from ~/Music/tts-biglinux and renders each entry as a card
with text preview, metadata, and an embedded audio player.
Supports grid/list toggle, multi-select with bulk delete, and open-in-folder.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango

from services.history_service import get_history_dir
from ui.audio_player import AudioPlayerWidget
from utils.i18n import _

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _show_in_file_manager(file_path: Path | None) -> None:
    """Open file manager highlighting the given file via D-Bus FileManager1.
    Falls back to xdg-open on the parent directory."""
    from gi.repository import Gio

    target = file_path or get_history_dir()
    uri = target.as_uri()

    try:
        proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.freedesktop.FileManager1",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1",
            None,
        )
        proxy.call_sync(
            "ShowItems",
            GLib.Variant("(ass)", ([uri], "")),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except Exception:
        # Fallback: xdg-open parent dir
        folder = target.parent if target.is_file() else target
        try:
            subprocess.Popen(
                ["xdg-open", str(folder)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass


class HistoryEntryRow(Gtk.ListBoxRow):
    """A single history entry card with text, metadata, and player."""

    def __init__(self, entry: dict, history_dir: Path, selection_mode: bool = False) -> None:
        super().__init__()
        self.set_activatable(False)
        self.set_selectable(False)
        self._entry = entry
        self._player: AudioPlayerWidget | None = None
        self._audio_file: Path | None = None

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("history-card")
        card.set_margin_top(4)
        card.set_margin_bottom(4)
        card.set_margin_start(2)
        card.set_margin_end(2)

        # ── Header row: checkbox + text preview + actions ────────
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        # Selection checkbox
        self._check = Gtk.CheckButton()
        self._check.set_visible(selection_mode)
        self._check.set_valign(Gtk.Align.START)
        header.append(self._check)

        text_preview = Gtk.Label(
            label=entry.get("text_preview", _("(no text)")),
        )
        text_preview.set_xalign(0)
        text_preview.set_hexpand(True)
        text_preview.set_wrap(True)
        text_preview.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        text_preview.set_max_width_chars(60)
        text_preview.set_lines(2)
        text_preview.set_ellipsize(Pango.EllipsizeMode.END)
        text_preview.add_css_class("history-text-preview")
        header.append(text_preview)

        # Action buttons box
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        actions.set_valign(Gtk.Align.START)

        # Open file location
        open_btn = Gtk.Button(icon_name="folder-open-symbolic")
        open_btn.add_css_class("flat")
        open_btn.add_css_class("circular")
        open_btn.set_tooltip_text(_("Open file location"))
        open_btn.connect("clicked", self._on_open_location)
        actions.append(open_btn)

        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.add_css_class("flat")
        delete_btn.add_css_class("circular")
        delete_btn.set_tooltip_text(_("Delete"))
        delete_btn.connect("clicked", self._on_delete)
        actions.append(delete_btn)

        header.append(actions)
        card.append(header)

        # ── Metadata row: badge + voice + timestamp ──────────────
        meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        meta_box.set_valign(Gtk.Align.CENTER)

        backend = entry.get("backend", "unknown")
        badge = Gtk.Label(label=backend)
        badge.add_css_class("history-badge")
        meta_box.append(badge)

        voice_id = entry.get("voice_id", "")
        if voice_id:
            voice_label = Gtk.Label(label=voice_id)
            voice_label.add_css_class("history-meta")
            voice_label.set_ellipsize(Pango.EllipsizeMode.END)
            voice_label.set_max_width_chars(20)
            meta_box.append(voice_label)

        timestamp = entry.get("timestamp", "")
        if timestamp:
            try:
                dt = datetime.strptime(timestamp, "%Y-%m-%d_%H-%M-%S")
                time_str = dt.strftime("%d/%m/%Y %H:%M")
            except ValueError:
                time_str = timestamp
            time_label = Gtk.Label(label=time_str)
            time_label.add_css_class("history-meta")
            time_label.set_hexpand(True)
            time_label.set_halign(Gtk.Align.END)
            meta_box.append(time_label)

        card.append(meta_box)

        # ── Audio player (if audio exists) ───────────────────────
        has_audio = entry.get("has_audio", False)
        if has_audio and timestamp:
            audio_file = self._find_audio_file(history_dir, timestamp, backend)
            if audio_file:
                self._audio_file = audio_file
                # Show filename
                fname_label = Gtk.Label(label=audio_file.name)
                fname_label.add_css_class("history-meta")
                fname_label.set_xalign(0)
                fname_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                fname_label.set_selectable(True)
                card.append(fname_label)
                self._player = AudioPlayerWidget(str(audio_file))
                card.append(self._player)

        self.set_child(card)

    @property
    def selected(self) -> bool:
        return self._check.get_active()

    @selected.setter
    def selected(self, value: bool) -> None:
        self._check.set_active(value)

    def set_selection_mode(self, active: bool) -> None:
        self._check.set_visible(active)
        if not active:
            self._check.set_active(False)

    def _find_audio_file(
        self, history_dir: Path, timestamp: str, backend: str
    ) -> Path | None:
        """Locate the audio file for this entry."""
        base = f"{timestamp}_{backend}"
        for ext in (".wav", ".mp3", ".ogg", ".flac"):
            path = history_dir / f"{base}{ext}"
            if path.is_file():
                return path
        return None

    def _on_open_location(self, _btn: Gtk.Button) -> None:
        """Open file manager highlighting the audio file."""
        target = self._audio_file if self._audio_file and self._audio_file.is_file() else None
        _show_in_file_manager(target)

    def _on_delete(self, _btn: Gtk.Button) -> None:
        """Delete this history entry and its files."""
        timestamp = self._entry.get("timestamp", "")
        backend = self._entry.get("backend", "")
        if not timestamp:
            return

        history_dir = get_history_dir()
        base = f"{timestamp}_{backend}"

        # Remove files
        for ext in (".wav", ".mp3", ".ogg", ".flac", ".txt"):
            path = history_dir / f"{base}{ext}"
            try:
                if path.is_file():
                    os.remove(path)
            except OSError as e:
                logger.warning("Failed to delete %s: %s", path, e)

        # Cleanup player
        if self._player:
            self._player.cleanup()

        # Signal parent to remove from list and index
        list_box = self.get_parent()
        if isinstance(list_box, Gtk.ListBox):
            list_box.remove(self)

        # Update index
        self._remove_from_index(history_dir, timestamp)

    def _remove_from_index(
        self, history_dir: Path, timestamp: str
    ) -> None:
        _remove_entry_from_index(history_dir, timestamp)

    def cleanup(self) -> None:
        if self._player:
            self._player.cleanup()


class HistoryGridCard(Gtk.FlowBoxChild):
    """Compact card for grid view."""

    def __init__(self, entry: dict, history_dir: Path, selection_mode: bool = False) -> None:
        super().__init__()
        self._entry = entry
        self._player: AudioPlayerWidget | None = None
        self._audio_file: Path | None = None

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("history-card")
        card.set_size_request(200, -1)
        card.set_margin_top(4)
        card.set_margin_bottom(4)
        card.set_margin_start(4)
        card.set_margin_end(4)

        # Top: checkbox
        self._check = Gtk.CheckButton()
        self._check.set_visible(selection_mode)
        self._check.set_halign(Gtk.Align.START)
        card.append(self._check)

        # Text preview
        text_label = Gtk.Label(
            label=entry.get("text_preview", _("(no text)")),
        )
        text_label.set_xalign(0)
        text_label.set_wrap(True)
        text_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        text_label.set_max_width_chars(30)
        text_label.set_lines(3)
        text_label.set_ellipsize(Pango.EllipsizeMode.END)
        text_label.add_css_class("history-text-preview")
        card.append(text_label)

        # Metadata
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        backend = entry.get("backend", "")
        if backend:
            badge = Gtk.Label(label=backend)
            badge.add_css_class("history-badge")
            meta.append(badge)

        timestamp = entry.get("timestamp", "")
        if timestamp:
            try:
                dt = datetime.strptime(timestamp, "%Y-%m-%d_%H-%M-%S")
                time_str = dt.strftime("%d/%m %H:%M")
            except ValueError:
                time_str = timestamp
            time_label = Gtk.Label(label=time_str)
            time_label.add_css_class("history-meta")
            time_label.set_hexpand(True)
            time_label.set_halign(Gtk.Align.END)
            meta.append(time_label)
        card.append(meta)

        # Audio player
        has_audio = entry.get("has_audio", False)
        if has_audio and timestamp:
            audio_file = self._find_audio_file(history_dir, timestamp, backend)
            if audio_file:
                self._audio_file = audio_file
                fname_label = Gtk.Label(label=audio_file.name)
                fname_label.add_css_class("history-meta")
                fname_label.set_xalign(0)
                fname_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                fname_label.set_selectable(True)
                card.append(fname_label)
                self._player = AudioPlayerWidget(str(audio_file))
                card.append(self._player)

        # Actions
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        actions.set_halign(Gtk.Align.END)

        open_btn = Gtk.Button(icon_name="folder-open-symbolic")
        open_btn.add_css_class("flat")
        open_btn.add_css_class("circular")
        open_btn.set_tooltip_text(_("Open file location"))
        open_btn.connect("clicked", self._on_open_location)
        actions.append(open_btn)

        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.add_css_class("flat")
        delete_btn.add_css_class("circular")
        delete_btn.set_tooltip_text(_("Delete"))
        delete_btn.connect("clicked", self._on_delete)
        actions.append(delete_btn)
        card.append(actions)

        self.set_child(card)

    @property
    def selected(self) -> bool:
        return self._check.get_active()

    @selected.setter
    def selected(self, value: bool) -> None:
        self._check.set_active(value)

    def set_selection_mode(self, active: bool) -> None:
        self._check.set_visible(active)
        if not active:
            self._check.set_active(False)

    def _find_audio_file(self, history_dir: Path, timestamp: str, backend: str) -> Path | None:
        base = f"{timestamp}_{backend}"
        for ext in (".wav", ".mp3", ".ogg", ".flac"):
            path = history_dir / f"{base}{ext}"
            if path.is_file():
                return path
        return None

    def _on_open_location(self, _btn: Gtk.Button) -> None:
        target = self._audio_file if self._audio_file and self._audio_file.is_file() else None
        _show_in_file_manager(target)

    def _on_delete(self, _btn: Gtk.Button) -> None:
        timestamp = self._entry.get("timestamp", "")
        backend = self._entry.get("backend", "")
        if not timestamp:
            return
        history_dir = get_history_dir()
        base = f"{timestamp}_{backend}"
        for ext in (".wav", ".mp3", ".ogg", ".flac", ".txt"):
            path = history_dir / f"{base}{ext}"
            try:
                if path.is_file():
                    os.remove(path)
            except OSError as e:
                logger.warning("Failed to delete %s: %s", path, e)
        if self._player:
            self._player.cleanup()
        flow = self.get_parent()
        if isinstance(flow, Gtk.FlowBox):
            flow.remove(self)
        _remove_entry_from_index(history_dir, timestamp)

    def cleanup(self) -> None:
        if self._player:
            self._player.cleanup()


def _remove_entry_from_index(history_dir: Path, timestamp: str) -> None:
    """Remove an entry from history.json by timestamp."""
    index_file = history_dir / "history.json"
    if not index_file.exists():
        return
    try:
        entries = json.loads(index_file.read_text(encoding="utf-8"))
        entries = [e for e in entries if e.get("timestamp") != timestamp]
        index_file.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to update history index: %s", e)


class HistoryView(Adw.NavigationPage):
    """History tab — shows list of past TTS entries with audio players."""

    def __init__(self) -> None:
        super().__init__()
        self.set_title(_("History"))
        self._rows: list[HistoryEntryRow] = []
        self._grid_cards: list[HistoryGridCard] = []
        self._all_entries: list[dict] = []
        self._grid_mode: bool = False
        self._selection_mode: bool = False
        self._build_ui()

    def _build_ui(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # ── Toolbar: search + view toggle + selection ────────────
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        toolbar.set_margin_top(12)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text(_("Search history…"))
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("search-changed", self._on_search_changed)
        toolbar.append(self._search_entry)

        # Grid/List toggle
        self._view_toggle = Gtk.ToggleButton(icon_name="view-grid-symbolic")
        self._view_toggle.add_css_class("flat")
        self._view_toggle.set_tooltip_text(_("Toggle grid/list view"))
        self._view_toggle.connect("toggled", self._on_view_toggle)
        toolbar.append(self._view_toggle)

        # Selection mode toggle
        self._select_toggle = Gtk.ToggleButton(icon_name="selection-mode-symbolic")
        self._select_toggle.add_css_class("flat")
        self._select_toggle.set_tooltip_text(_("Select items"))
        self._select_toggle.connect("toggled", self._on_select_toggle)
        toolbar.append(self._select_toggle)

        # Refresh
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text(_("Refresh"))
        refresh_btn.connect("clicked", lambda _: self.reload())
        toolbar.append(refresh_btn)

        outer.append(toolbar)

        # ── Selection action bar (hidden by default) ─────────────
        self._selection_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._selection_bar.set_margin_start(12)
        self._selection_bar.set_margin_end(12)
        self._selection_bar.set_margin_top(6)
        self._selection_bar.set_visible(False)

        select_all_btn = Gtk.Button(label=_("Select All"))
        select_all_btn.add_css_class("flat")
        select_all_btn.connect("clicked", self._on_select_all)
        self._selection_bar.append(select_all_btn)

        deselect_btn = Gtk.Button(label=_("Deselect All"))
        deselect_btn.add_css_class("flat")
        deselect_btn.connect("clicked", self._on_deselect_all)
        self._selection_bar.append(deselect_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self._selection_bar.append(spacer)

        delete_sel_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_sel_btn.add_css_class("destructive-action")
        delete_sel_btn.set_tooltip_text(_("Delete selected"))
        delete_sel_btn.connect("clicked", self._on_delete_selected)
        self._selection_bar.append(delete_sel_btn)

        outer.append(self._selection_bar)

        # ── Content stack (list / grid) ──────────────────────────
        self._content_stack = Gtk.Stack()
        self._content_stack.set_vexpand(True)
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        # List view
        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_vexpand(True)

        list_clamp = Adw.Clamp()
        list_clamp.set_maximum_size(700)
        list_clamp.set_tightening_threshold(500)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.add_css_class("boxed-list")
        self._list_box.set_margin_start(12)
        self._list_box.set_margin_end(12)
        self._list_box.set_margin_top(8)
        self._list_box.set_margin_bottom(24)
        self._list_box.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("History entries")]
        )

        list_clamp.set_child(self._list_box)
        list_scroll.set_child(list_clamp)
        self._content_stack.add_named(list_scroll, "list")

        # Grid view
        grid_scroll = Gtk.ScrolledWindow()
        grid_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        grid_scroll.set_vexpand(True)

        self._flow_box = Gtk.FlowBox()
        self._flow_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow_box.set_homogeneous(True)
        self._flow_box.set_min_children_per_line(2)
        self._flow_box.set_max_children_per_line(4)
        self._flow_box.set_column_spacing(8)
        self._flow_box.set_row_spacing(8)
        self._flow_box.set_margin_start(12)
        self._flow_box.set_margin_end(12)
        self._flow_box.set_margin_top(8)
        self._flow_box.set_margin_bottom(24)
        self._flow_box.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("History entries")]
        )

        grid_scroll.set_child(self._flow_box)
        self._content_stack.add_named(grid_scroll, "grid")

        # ── Empty state ──────────────────────────────────────────
        self._empty_page = Adw.StatusPage()
        self._empty_page.set_icon_name("document-open-recent-symbolic")
        self._empty_page.set_title(_("No History"))
        self._empty_page.set_description(
            _("Spoken texts will appear here when history is enabled.")
        )

        self._outer_stack = Gtk.Stack()
        self._outer_stack.set_vexpand(True)
        self._outer_stack.add_named(self._empty_page, "empty")
        self._outer_stack.add_named(self._content_stack, "content")

        outer.append(self._outer_stack)
        self.set_child(outer)

    def reload(self) -> None:
        """Reload history entries from disk."""
        self._cleanup_all()

        history_dir = get_history_dir()
        index_file = history_dir / "history.json"

        if not index_file.exists():
            self._all_entries = []
            self._outer_stack.set_visible_child_name("empty")
            return

        try:
            self._all_entries = json.loads(
                index_file.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            self._all_entries = []

        if not self._all_entries:
            self._outer_stack.set_visible_child_name("empty")
            return

        self._outer_stack.set_visible_child_name("content")
        self._populate(self._all_entries, history_dir)
        self._apply_view_mode()

    def _cleanup_all(self) -> None:
        for row in self._rows:
            row.cleanup()
        self._rows.clear()
        for card in self._grid_cards:
            card.cleanup()
        self._grid_cards.clear()
        # Clear list
        while (child := self._list_box.get_first_child()):
            self._list_box.remove(child)
        # Clear grid
        while (child := self._flow_box.get_first_child()):
            self._flow_box.remove(child)

    def _populate(self, entries: list[dict], history_dir: Path) -> None:
        """Populate both list and grid with entry widgets (newest first)."""
        sel = self._selection_mode
        for entry in reversed(entries):
            row = HistoryEntryRow(entry, history_dir, selection_mode=sel)
            self._list_box.append(row)
            self._rows.append(row)

            card = HistoryGridCard(entry, history_dir, selection_mode=sel)
            self._flow_box.append(card)
            self._grid_cards.append(card)

    def _apply_view_mode(self) -> None:
        self._content_stack.set_visible_child_name("grid" if self._grid_mode else "list")

    # ── Toolbar handlers ─────────────────────────────────────────

    def _on_view_toggle(self, btn: Gtk.ToggleButton) -> None:
        self._grid_mode = btn.get_active()
        btn.set_icon_name(
            "view-list-symbolic" if self._grid_mode else "view-grid-symbolic"
        )
        self._apply_view_mode()

    def _on_select_toggle(self, btn: Gtk.ToggleButton) -> None:
        self._selection_mode = btn.get_active()
        self._selection_bar.set_visible(self._selection_mode)
        for row in self._rows:
            row.set_selection_mode(self._selection_mode)
        for card in self._grid_cards:
            card.set_selection_mode(self._selection_mode)

    def _on_select_all(self, _btn: Gtk.Button) -> None:
        items = self._grid_cards if self._grid_mode else self._rows
        for item in items:
            if item.get_visible():
                item.selected = True

    def _on_deselect_all(self, _btn: Gtk.Button) -> None:
        items = self._grid_cards if self._grid_mode else self._rows
        for item in items:
            item.selected = False

    def _on_delete_selected(self, _btn: Gtk.Button) -> None:
        """Delete all selected entries."""
        history_dir = get_history_dir()
        # Collect timestamps to delete from active view
        items = self._grid_cards if self._grid_mode else self._rows
        to_delete: list[str] = []
        for item in items:
            if item.selected:
                ts = item._entry.get("timestamp", "")
                if ts:
                    to_delete.append(ts)

        if not to_delete:
            return

        # Delete files
        for ts in to_delete:
            for item in list(self._rows):
                if item._entry.get("timestamp") == ts:
                    if item._player:
                        item._player.cleanup()
                    self._list_box.remove(item)
                    self._rows.remove(item)
                    break
            for item in list(self._grid_cards):
                if item._entry.get("timestamp") == ts:
                    if item._player:
                        item._player.cleanup()
                    self._flow_box.remove(item)
                    self._grid_cards.remove(item)
                    break

            # Remove associated files
            for entry in self._all_entries:
                if entry.get("timestamp") == ts:
                    backend = entry.get("backend", "")
                    base = f"{ts}_{backend}"
                    for ext in (".wav", ".mp3", ".ogg", ".flac", ".txt"):
                        path = history_dir / f"{base}{ext}"
                        try:
                            if path.is_file():
                                os.remove(path)
                        except OSError:
                            pass
                    break

        # Update index
        self._all_entries = [
            e for e in self._all_entries
            if e.get("timestamp") not in to_delete
        ]
        index_file = history_dir / "history.json"
        try:
            index_file.write_text(
                json.dumps(self._all_entries, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("Failed to update history index: %s", e)

        if not self._all_entries:
            self._outer_stack.set_visible_child_name("empty")

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().strip().lower()
        if not query:
            self._filter_items(None)
            return
        self._filter_items(query)

    def _filter_items(self, query: str | None) -> None:
        """Show/hide rows and cards based on search query."""
        visible_count = 0
        for row, card in zip(self._rows, self._grid_cards):
            if query is None:
                row.set_visible(True)
                card.set_visible(True)
                visible_count += 1
            else:
                text = row._entry.get("text_preview", "").lower()
                backend = row._entry.get("backend", "").lower()
                voice = row._entry.get("voice_id", "").lower()
                match = query in text or query in backend or query in voice
                row.set_visible(match)
                card.set_visible(match)
                if match:
                    visible_count += 1

        if visible_count == 0 and query:
            self._empty_page.set_title(_("No Results"))
            self._empty_page.set_description(
                _("No entries match your search.")
            )
            self._outer_stack.set_visible_child_name("empty")
        elif visible_count == 0:
            self._empty_page.set_title(_("No History"))
            self._empty_page.set_description(
                _("Spoken texts will appear here when history is enabled.")
            )
            self._outer_stack.set_visible_child_name("empty")
        else:
            self._outer_stack.set_visible_child_name("content")

    def cleanup(self) -> None:
        """Release all player resources."""
        self._cleanup_all()
