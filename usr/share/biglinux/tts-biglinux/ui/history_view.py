"""History — every reading kept, with its text, voice, engine and audio.

Entries come from history_service (SQLite index + files), a page at a time,
filtered and searched in the database off the main thread. Each card shows
the text (expandable), engine, voice, date, duration and outcome, an inline
player, and actions: copy, read again, save audio, open the folder, delete.
Deleting one entry can be undone for a few seconds; deleting a selection asks
first. Deleted files go to the Trash. The view refreshes itself when a
reading is saved.
"""

# ruff: noqa: E402  # gi.require_version must run before repository imports.

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from services import history_service
from services.history_service import parse_timestamp
from services.tts_service import engine_name
from ui.audio_player import AudioPlayerWidget
from utils.i18n import _

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
_UNDO_SECONDS = 5

def _engine_label(backend: str) -> str:
    return engine_name(backend) if backend else _("Unknown")


def _voice_label(voice_id: str) -> str:
    """ "kokoro:pf_dora" → "pf_dora", "piper:/…/pt_BR-faber-medium.onnx" → "pt_BR-faber-medium"."""
    name = voice_id.split(":", 1)[-1].removeprefix("espeak-")
    return Path(name).name.removesuffix(".onnx") if "/" in name else name


def _when_label(timestamp: str) -> str:
    dt = parse_timestamp(timestamp)
    if dt is None:
        return timestamp
    today = datetime.now().date()
    clock = dt.strftime("%H:%M")
    if dt.date() == today:
        return _("Today, {time}").format(time=clock)
    if dt.date() == today - timedelta(days=1):
        return _("Yesterday, {time}").format(time=clock)
    return dt.strftime("%x %H:%M")  # locale date order


def _spinner() -> Gtk.Widget:
    # Adw.Spinner needs libadwaita 1.6; the package accepts 1.5.
    return Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)


def _show_in_file_manager(file_path: Path | None) -> None:
    """Show the file in the file manager (FileManager1 D-Bus, or xdg-open)."""
    target = file_path or history_service.get_history_dir()
    try:
        proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
            "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1", None,
        )
        proxy.call_sync(
            "ShowItems", GLib.Variant("(ass)", ([target.as_uri()], "")),
            Gio.DBusCallFlags.NONE, -1, None,
        )
    except Exception:
        folder = target.parent if target.is_file() else target
        try:
            subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass


# ── One entry ─────────────────────────────────────────────────────────

class HistoryCard(Gtk.Box):
    """A history entry: text, details, player and actions."""

    def __init__(self, entry: dict, view: HistoryView) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("history-card")
        self.entry = entry
        self._view = view
        self._player: AudioPlayerWidget | None = None
        self._audio = history_service.audio_path(entry) if entry.get("has_audio") else None
        text = entry.get("text") or entry.get("text_preview") or ""

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.check = Gtk.CheckButton()
        self.check.set_valign(Gtk.Align.START)
        self.check.set_visible(view.selection_mode)
        self.check.update_property([Gtk.AccessibleProperty.LABEL], [_("Select this reading")])
        self.check.connect("toggled", lambda _c: view.update_selection())
        top.append(self.check)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)
        self._text = Gtk.Label(label=text or _("(text not kept)"), xalign=0)
        self._text.set_wrap(True)
        self._text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._text.set_lines(3)
        self._text.set_ellipsize(Pango.EllipsizeMode.END)
        self._text.set_selectable(True)
        self._text.add_css_class("history-text-preview")
        if not text:
            self._text.add_css_class("dim-label")
        text_box.append(self._text)
        if len(text) > 180 or text.count("\n") > 2:
            self._more = Gtk.Button(label=_("Show more"))
            self._more.add_css_class("flat")
            self._more.add_css_class("history-more")
            self._more.set_halign(Gtk.Align.START)
            self._more.connect("clicked", self._on_toggle_text)
            text_box.append(self._more)
        top.append(text_box)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        actions.set_valign(Gtk.Align.START)
        if text:
            copy = self._icon_button("edit-copy-symbolic", _("Copy text"), self._on_copy)
            actions.append(copy)
            again = self._icon_button("media-playlist-repeat-symbolic", _("Read again with the current voice"),
                                      self._on_read_again)
            actions.append(again)
        menu = Gtk.MenuButton(icon_name="view-more-symbolic")
        menu.add_css_class("flat")
        menu.add_css_class("circular")
        menu.set_tooltip_text(_("More actions"))
        menu.update_property([Gtk.AccessibleProperty.LABEL], [_("More actions")])
        menu.set_popover(self._actions_popover())
        actions.append(menu)
        top.append(actions)
        self.append(top)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        badge = Gtk.Label(label=_engine_label(entry.get("backend", "")))
        badge.add_css_class("history-badge")
        meta.append(badge)
        voice = entry.get("voice_id", "")
        if voice:
            voice_label = Gtk.Label(label=_voice_label(voice), xalign=0)
            voice_label.set_tooltip_text(voice)
            voice_label.set_ellipsize(Pango.EllipsizeMode.END)
            voice_label.set_max_width_chars(24)
            voice_label.add_css_class("history-meta")
            meta.append(voice_label)
        status = entry.get("status", "completed")
        if status in ("stopped", "error"):
            pill = Gtk.Label(label=_("Stopped") if status == "stopped" else _("Error"))
            pill.add_css_class("history-status")
            pill.add_css_class(status)
            pill.set_tooltip_text(
                _("Reading stopped before the end") if status == "stopped"
                else _("The engine failed during this reading")
            )
            meta.append(pill)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        meta.append(spacer)
        when = Gtk.Label(label=_when_label(entry.get("timestamp", "")))
        when.add_css_class("history-meta")
        meta.append(when)
        self.append(meta)

        if self._audio is not None:
            self._player = AudioPlayerWidget(str(self._audio))
            self._player.set_duration_hint(entry.get("duration") or 0.0)
            self.append(self._player)
        elif entry.get("has_audio") is False and text:
            hint = Gtk.Label(label=_("Audio not kept for this reading"), xalign=0)
            hint.add_css_class("history-meta")
            self.append(hint)

        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("{engine} reading, {when}").format(
                engine=_engine_label(entry.get("backend", "")),
                when=_when_label(entry.get("timestamp", "")))],
        )

    @staticmethod
    def _icon_button(icon: str, tooltip: str, callback: Callable) -> Gtk.Button:
        button = Gtk.Button(icon_name=icon)
        button.add_css_class("flat")
        button.add_css_class("circular")
        button.set_tooltip_text(tooltip)
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
        button.connect("clicked", lambda _b: callback())
        return button

    def _actions_popover(self) -> Gtk.Popover:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        popover = Gtk.Popover()

        def item(label: str, callback: Callable, sensitive: bool = True, destructive: bool = False) -> None:
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            if destructive:
                b.add_css_class("destructive-action")
            b.get_child().set_xalign(0)
            b.set_sensitive(sensitive)
            b.connect("clicked", lambda _b: (popover.popdown(), callback()))
            box.append(b)

        item(_("Save audio as…"), self._on_export, self._audio is not None)
        item(_("Open file location"), self._on_open_location)
        item(_("Delete"), lambda: self._view.delete_with_undo(self), destructive=True)
        popover.set_child(box)
        return popover

    def _on_toggle_text(self, button: Gtk.Button) -> None:
        expanded = self._text.get_lines() == -1
        self._text.set_lines(3 if expanded else -1)
        self._text.set_ellipsize(Pango.EllipsizeMode.END if expanded else Pango.EllipsizeMode.NONE)
        button.set_label(_("Show more") if expanded else _("Show less"))

    def _on_copy(self) -> None:
        text = history_service.entry_text(self.entry)
        Gdk.Display.get_default().get_clipboard().set(text)
        self._view.toast(_("Text copied"))

    def _on_read_again(self) -> None:
        if self._view.on_read_again:
            self._view.on_read_again(history_service.entry_text(self.entry))

    def _on_export(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Save audio as"))
        dialog.set_initial_name(f"{self.entry.get('timestamp', 'reading')}.wav")

        def done(d: Gtk.FileDialog, result) -> None:
            try:
                file = d.save_finish(result)
            except GLib.Error:
                return  # cancelled
            ok = history_service.export_audio(self.entry, file.get_path())
            self._view.toast(_("Audio saved") if ok else _("Could not save the audio"))

        dialog.save(self.get_root(), None, done)

    def _on_open_location(self) -> None:
        _show_in_file_manager(self._audio)

    @property
    def selected(self) -> bool:
        return self.check.get_active()

    def set_selection_mode(self, active: bool) -> None:
        self.check.set_visible(active)
        if not active:
            self.check.set_active(False)

    def cleanup(self) -> None:
        if self._player:
            self._player.cleanup()


# ── The view ──────────────────────────────────────────────────────────

class HistoryView(Adw.NavigationPage):
    """The History page: filters, the list of readings and their actions."""

    # Date filter: (label, days back or None). "Yesterday" is a single day.
    _PERIODS = ("all", "today", "yesterday", "week")

    def __init__(self, settings=None) -> None:
        super().__init__()
        self.set_title(_("History"))
        self._settings = settings
        self.on_read_again: Callable[[str], None] | None = None
        self.on_enable_history: Callable[[], None] | None = None
        self.selection_mode = False
        self._cards: list[HistoryCard] = []
        self._grid_mode = False
        self._newest_first = True
        self._offset = 0
        self._total = 0
        self._loading = False
        self._query_serial = 0
        self._search_debounce_id = 0
        self._dirty = True
        self._backends: list[str] = []
        self._pending_delete: dict[str, HistoryCard] = {}
        self._build_ui()
        history_service.add_listener(self._on_history_changed)

    # ── Layout ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._toasts = Adw.ToastOverlay()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self._banner = Adw.Banner()
        self._banner.set_title(_("Saving is off: new readings are not kept."))
        self._banner.set_button_label(_("Turn On"))
        self._banner.connect("button-clicked", lambda _b: self.on_enable_history and self.on_enable_history())
        outer.append(self._banner)

        # Search + filters (wrap on narrow windows).
        bar = Gtk.FlowBox()
        bar.set_selection_mode(Gtk.SelectionMode.NONE)
        bar.set_max_children_per_line(6)
        bar.set_column_spacing(6)
        bar.set_row_spacing(6)
        bar.set_margin_start(12)
        bar.set_margin_end(12)
        bar.set_margin_top(12)
        bar.add_css_class("history-toolbar")

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text(_("Search text, voice, engine or date…"))
        self._search.set_hexpand(True)
        self._search.set_size_request(220, -1)
        self._search.connect("search-changed", self._on_search_changed)
        bar.append(self._search)

        self._period = Gtk.DropDown.new_from_strings(
            [_("All dates"), _("Today"), _("Yesterday"), _("Last 7 days")]
        )
        self._period.set_tooltip_text(_("Filter by date"))
        self._period.update_property([Gtk.AccessibleProperty.LABEL], [_("Filter by date")])
        self._period.connect("notify::selected", lambda *_a: self.reload())
        bar.append(self._period)

        self._engine_model = Gtk.StringList.new([_("All engines")])
        self._engine = Gtk.DropDown.new(self._engine_model, None)
        self._engine.set_tooltip_text(_("Filter by engine"))
        self._engine.update_property([Gtk.AccessibleProperty.LABEL], [_("Filter by engine")])
        self._engine.connect("notify::selected", lambda *_a: self.reload())
        bar.append(self._engine)

        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._sort = Gtk.ToggleButton(icon_name="view-sort-descending-symbolic")
        self._sort.add_css_class("flat")
        self._sort.set_tooltip_text(_("Newest first"))
        self._sort.update_property([Gtk.AccessibleProperty.LABEL], [_("Sort order")])
        self._sort.connect("toggled", self._on_sort_toggled)
        tools.append(self._sort)
        self._view_toggle = Gtk.ToggleButton(icon_name="view-grid-symbolic")
        self._view_toggle.add_css_class("flat")
        self._view_toggle.set_tooltip_text(_("Show as grid"))
        self._view_toggle.update_property([Gtk.AccessibleProperty.LABEL], [_("Show as grid")])
        self._view_toggle.connect("toggled", self._on_view_toggled)
        tools.append(self._view_toggle)
        self._select_toggle = Gtk.ToggleButton(icon_name="edit-select-all-symbolic")
        self._select_toggle.add_css_class("flat")
        self._select_toggle.set_tooltip_text(_("Select readings"))
        self._select_toggle.update_property([Gtk.AccessibleProperty.LABEL], [_("Select readings")])
        self._select_toggle.connect("toggled", self._on_select_toggled)
        tools.append(self._select_toggle)
        bar.append(tools)
        outer.append(bar)

        # Selection actions.
        self._selection_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._selection_bar.set_margin_start(12)
        self._selection_bar.set_margin_end(12)
        self._selection_bar.set_margin_top(6)
        self._selection_bar.set_visible(False)
        select_all = Gtk.Button(label=_("Select All"))
        select_all.add_css_class("flat")
        select_all.connect("clicked", lambda _b: self._select_all(True))
        self._selection_bar.append(select_all)
        none = Gtk.Button(label=_("Deselect All"))
        none.add_css_class("flat")
        none.connect("clicked", lambda _b: self._select_all(False))
        self._selection_bar.append(none)
        self._selected_label = Gtk.Label(xalign=0)
        self._selected_label.set_hexpand(True)
        self._selected_label.add_css_class("dim-label")
        self._selection_bar.append(self._selected_label)
        self._export_selected = Gtk.Button(icon_name="document-save-symbolic")
        self._export_selected.set_tooltip_text(_("Save the selected readings to a folder"))
        self._export_selected.update_property([Gtk.AccessibleProperty.LABEL], [_("Save selected")])
        self._export_selected.connect("clicked", self._on_export_selected)
        self._selection_bar.append(self._export_selected)
        self._delete_selected = Gtk.Button(icon_name="user-trash-symbolic")
        self._delete_selected.add_css_class("destructive-action")
        self._delete_selected.set_tooltip_text(_("Delete selected"))
        self._delete_selected.update_property([Gtk.AccessibleProperty.LABEL], [_("Delete selected")])
        self._delete_selected.connect("clicked", self._on_delete_selected)
        self._selection_bar.append(self._delete_selected)
        outer.append(self._selection_bar)

        # Entries: list (default) or grid; the next page loads at the bottom.
        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        self._list.update_property([Gtk.AccessibleProperty.LABEL], [_("History entries")])
        self._grid = Gtk.FlowBox()
        self._grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self._grid.set_homogeneous(True)
        self._grid.set_min_children_per_line(1)
        self._grid.set_max_children_per_line(3)
        self._grid.set_column_spacing(8)
        self._grid.set_row_spacing(8)
        self._grid.set_visible(False)
        self._grid.update_property([Gtk.AccessibleProperty.LABEL], [_("History entries")])
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(8)
        content.set_margin_bottom(24)
        content.append(self._list)
        content.append(self._grid)
        self._more_spinner = _spinner()
        self._more_spinner.set_visible(False)
        content.append(self._more_spinner)
        self._clamp = Adw.Clamp()
        self._clamp.set_maximum_size(760)
        self._clamp.set_child(content)
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_vexpand(True)
        self._scroll.set_child(self._clamp)
        self._scroll.connect("edge-reached", self._on_edge_reached)

        self._empty = Adw.StatusPage()
        self._empty.set_icon_name("document-open-recent-symbolic")
        self._empty_button = Gtk.Button()
        self._empty_button.add_css_class("pill")
        self._empty_button.add_css_class("suggested-action")
        self._empty_button.set_halign(Gtk.Align.CENTER)
        self._empty_button.connect("clicked", self._on_empty_action)
        self._empty.set_child(self._empty_button)

        self._loading_page = _spinner()
        self._loading_page.set_halign(Gtk.Align.CENTER)
        self._loading_page.set_valign(Gtk.Align.CENTER)

        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)
        self._stack.add_named(self._loading_page, "loading")
        self._stack.add_named(self._empty, "empty")
        self._stack.add_named(self._scroll, "content")
        outer.append(self._stack)

        self._toasts.set_child(outer)
        self.set_child(self._toasts)
        self._update_banner()

    # ── Data ────────────────────────────────────────────────────────

    def _history_enabled(self) -> bool:
        return bool(self._settings and self._settings.history.enabled)

    def _update_banner(self) -> None:
        self._banner.set_revealed(bool(self._settings) and not self._history_enabled())

    def set_history_enabled(self, _enabled: bool) -> None:
        """The "Save history" setting changed."""
        self._update_banner()
        if self._stack.get_visible_child_name() == "empty":
            self._show_empty()

    def _filters(self) -> dict:
        filters: dict = {"newest_first": self._newest_first}
        query = self._search.get_text().strip()
        if query:
            filters["query"] = query
        today = datetime.combine(datetime.now().date(), datetime.min.time()).timestamp()
        period = self._PERIODS[self._period.get_selected()] if self._period.get_selected() < 4 else "all"
        if period == "today":
            filters["since"] = today
        elif period == "yesterday":
            filters["since"], filters["until"] = today - 86400, today
        elif period == "week":
            filters["since"] = today - 6 * 86400
        idx = self._engine.get_selected()
        if 0 < idx <= len(self._backends):
            filters["backend"] = self._backends[idx - 1]
        return filters

    def _has_filters(self) -> bool:
        f = self._filters()
        return any(k in f for k in ("query", "since", "until", "backend"))

    def reload(self) -> None:
        """Load the first page again (filters kept)."""
        self._dirty = False
        self._clear()
        self._offset = 0
        self._update_banner()
        if self._stack.get_visible_child_name() != "content":
            self._stack.set_visible_child_name("loading")
        self._load_page(first=True)

    def _load_page(self, first: bool = False) -> None:
        self._query_serial += 1
        serial = self._query_serial
        filters = self._filters()
        offset = self._offset
        self._loading = True

        def work() -> None:
            try:
                rows = history_service.load_history_entries(limit=PAGE_SIZE, offset=offset, **filters)
                total = history_service.count_history_entries(
                    **{k: v for k, v in filters.items() if k != "newest_first"}
                ) if first else None
                from services import history_db

                backends = history_db.backends() if first else None
                error = ""
            except Exception as e:  # unreadable folder, damaged index…
                logger.error("Failed to load history: %s", e)
                rows, total, backends, error = [], 0, None, str(e)
            GLib.idle_add(self._on_page, serial, rows, total, backends, error)

        threading.Thread(target=work, daemon=True).start()

    def _on_page(self, serial: int, rows: list[dict], total: int | None, backends, error: str) -> bool:
        if serial != self._query_serial:
            return False  # a newer search replaced this one
        self._loading = False
        self._more_spinner.set_visible(False)
        if backends is not None:
            self._set_backends(backends)
        if total is not None:
            self._total = total
        for entry in rows:
            if entry["id"] not in self._pending_delete:
                self._add_card(entry)
        self._offset += len(rows)
        if error:
            self._show_empty(error)
        elif not self._cards:
            self._show_empty()
        else:
            self._stack.set_visible_child_name("content")
        self.update_selection()
        return False

    def _set_backends(self, backends: list[str]) -> None:
        if backends == self._backends:
            return
        current = self._filters().get("backend")
        self._backends = backends
        self._engine_model.splice(1, self._engine_model.get_n_items() - 1, [_engine_label(b) for b in backends])
        if current in backends:
            self._engine.set_selected(backends.index(current) + 1)

    def _add_card(self, entry: dict) -> None:
        card = HistoryCard(entry, self)
        self._cards.append(card)
        if self._grid_mode:
            self._grid.append(card)
        else:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_child(card)
            self._list.append(row)

    def _clear(self) -> None:
        for card in self._cards:
            card.cleanup()
        self._cards.clear()
        self._list.remove_all()
        self._grid.remove_all()

    def _show_empty(self, error: str = "") -> None:
        if error:
            self._empty.set_title(_("The history could not be read"))
            self._empty.set_description(
                _("Check the permissions of the folder {folder}.").format(
                    folder=GLib.markup_escape_text(history_service.display_history_dir()))
                + "\n" + GLib.markup_escape_text(error)
            )
            self._empty_button.set_visible(False)
        elif self._has_filters():
            self._empty.set_icon_name("edit-find-symbolic")
            self._empty.set_title(_("No readings found"))
            self._empty.set_description(_("Try other words or another date or engine."))
            self._empty_button.set_label(_("Clear Filters"))
            self._empty_button.set_visible(True)
        else:
            self._empty.set_icon_name("document-open-recent-symbolic")
            self._empty.set_title(_("No readings yet"))
            where = GLib.markup_escape_text(history_service.display_history_dir())
            if self._history_enabled():
                self._empty.set_description(
                    _("Everything you listen to will appear here, with its audio. It is stored only "
                      "on this computer, in {folder}.").format(folder=where)
                )
                self._empty_button.set_visible(False)
            else:
                self._empty.set_description(
                    _("Saving is off. Turn it on to keep what you listen to, with its audio — only "
                      "on this computer, in {folder}.").format(folder=where)
                )
                self._empty_button.set_label(_("Turn On History"))
                self._empty_button.set_visible(bool(self.on_enable_history))
        self._stack.set_visible_child_name("empty")

    def _on_empty_action(self, _button: Gtk.Button) -> None:
        if self._has_filters():
            self._search.set_text("")
            self._period.set_selected(0)
            self._engine.set_selected(0)
            self.reload()
        elif self.on_enable_history:
            self.on_enable_history()

    def _on_history_changed(self) -> None:
        """A reading was saved or entries changed (main thread)."""
        if self.get_mapped():
            if not self._loading and not self.selection_mode:
                self.reload()
        else:
            self._dirty = True

    def show(self) -> None:
        """The page is being shown: refresh if something changed meanwhile."""
        if self._dirty or not self._cards:
            self.reload()

    def _on_edge_reached(self, _scroll: Gtk.ScrolledWindow, pos: Gtk.PositionType) -> None:
        if pos == Gtk.PositionType.BOTTOM and not self._loading and self._offset < self._total:
            self._more_spinner.set_visible(True)
            self._load_page()

    # ── Filters and modes ───────────────────────────────────────────

    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        if self._search_debounce_id:
            GLib.source_remove(self._search_debounce_id)

        def run() -> bool:
            self._search_debounce_id = 0
            self.reload()
            return False

        self._search_debounce_id = GLib.timeout_add(250, run)

    def _on_sort_toggled(self, button: Gtk.ToggleButton) -> None:
        self._newest_first = not button.get_active()
        button.set_icon_name("view-sort-descending-symbolic" if self._newest_first else "view-sort-ascending-symbolic")
        button.set_tooltip_text(_("Newest first") if self._newest_first else _("Oldest first"))
        self.reload()

    def _on_view_toggled(self, button: Gtk.ToggleButton) -> None:
        self._grid_mode = button.get_active()
        self._list.set_visible(not self._grid_mode)
        self._grid.set_visible(self._grid_mode)
        button.set_tooltip_text(_("Show as list") if self._grid_mode else _("Show as grid"))
        button.set_icon_name("format-justify-fill-symbolic" if self._grid_mode else "view-grid-symbolic")
        self.reload()

    def _on_select_toggled(self, button: Gtk.ToggleButton) -> None:
        self.selection_mode = button.get_active()
        self._selection_bar.set_visible(self.selection_mode)
        for card in self._cards:
            card.set_selection_mode(self.selection_mode)
        self.update_selection()

    def _select_all(self, value: bool) -> None:
        for card in self._cards:
            card.check.set_active(value)

    def _selected(self) -> list[HistoryCard]:
        return [c for c in self._cards if c.selected]

    def update_selection(self) -> None:
        n = len(self._selected())
        self._selected_label.set_label(_("Selected: {count}").format(count=n) if n else "")
        self._delete_selected.set_sensitive(n > 0)
        self._export_selected.set_sensitive(n > 0)

    # ── Deleting and saving ────────────────────────────────────────

    def toast(self, text: str) -> None:
        self._toasts.add_toast(Adw.Toast.new(text))

    def _remove_card(self, card: HistoryCard) -> None:
        card.cleanup()
        if card in self._cards:
            self._cards.remove(card)
        parent = card.get_parent()
        if isinstance(parent, Gtk.ListBoxRow):
            self._list.remove(parent)
        elif isinstance(parent, Gtk.FlowBoxChild):
            self._grid.remove(parent)

    def delete_with_undo(self, card: HistoryCard) -> None:
        """Hide the entry now; delete it when the Undo toast goes away."""
        entry_id = card.entry["id"]
        self._pending_delete[entry_id] = card
        holder = card.get_parent()
        holder.set_visible(False)
        toast = Adw.Toast.new(_("Reading deleted"))
        toast.set_button_label(_("Undo"))
        toast.set_timeout(_UNDO_SECONDS)
        undone = {"value": False}

        def undo(_t) -> None:
            undone["value"] = True
            self._pending_delete.pop(entry_id, None)
            holder.set_visible(True)

        def dismissed(_t) -> None:
            if undone["value"]:
                return
            self._pending_delete.pop(entry_id, None)
            self._remove_card(card)
            threading.Thread(target=history_service.delete_entries, args=([entry_id],), daemon=True).start()
            if not self._cards:
                self._show_empty()

        toast.connect("button-clicked", undo)
        toast.connect("dismissed", dismissed)
        self._undo_toast = toast
        self._toasts.add_toast(toast)

    def _on_delete_selected(self, _button: Gtk.Button) -> None:
        cards = self._selected()
        if not cards:
            return
        dialog = Adw.AlertDialog.new(
            _("Delete the selected readings?"),
            _("Readings: {count}. Their audio and text files are moved to the Trash.").format(count=len(cards)),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response: str) -> None:
            if response != "delete":
                return
            ids = [c.entry["id"] for c in cards]
            for card in cards:
                self._remove_card(card)
            threading.Thread(target=history_service.delete_entries, args=(ids,), daemon=True).start()
            self.toast(_("Moved to the Trash: {count}").format(count=len(ids)))
            self.update_selection()
            if not self._cards:
                self._show_empty()

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _on_export_selected(self, _button: Gtk.Button) -> None:
        entries = [c.entry for c in self._selected()]
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Save the selected readings"))

        def done(d: Gtk.FileDialog, result) -> None:
            try:
                folder = Path(d.select_folder_finish(result).get_path())
            except GLib.Error:
                return  # cancelled

            def work() -> None:
                saved = 0
                for entry in entries:
                    base = folder / f"{entry['timestamp']}_{entry['backend']}"
                    if history_service.export_audio(entry, str(base.with_suffix(".wav"))):
                        saved += 1
                    text = history_service.entry_text(entry)
                    if text:
                        try:
                            base.with_suffix(".txt").write_text(text, encoding="utf-8")
                        except OSError as e:
                            logger.warning("Could not save %s: %s", base, e)
                GLib.idle_add(self.toast, _("Saved to {folder}: {count}").format(folder=folder.name, count=saved))

            threading.Thread(target=work, daemon=True).start()

        dialog.select_folder(self.get_root(), None, done)
