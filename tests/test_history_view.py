"""The History page against real widgets (GTK 4 / libadwaita).

Skipped without a display — run headless with GDK_BACKEND=broadway.
"""
import importlib
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display for GTK", allow_module_level=True)
Adw.init()

from fake_engines import text_wav  # noqa: E402

config = importlib.import_module("config")
hs = importlib.import_module("services.history_service")
hdb = importlib.import_module("services.history_db")
hv = importlib.import_module("ui.history_view")


def _pump(condition=lambda: False, timeout=3.0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.iteration(False):
            pass
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def _add(text, backend="piper", days_ago=0, audio=True, tmp=None):
    path = text_wav(tmp / f"{abs(hash(text))}.wav", "a") if audio else None
    hs.save_history_entry(text=text, audio_path=path, backend=backend, voice_id=f"{backend}:voz")
    if days_ago:
        with hdb._connect() as conn:
            conn.execute("UPDATE entries SET created_at = created_at - ? WHERE text = ?", (days_ago * 86400, text))


@pytest.fixture
def page(tmp_path):
    settings = config.AppSettings()
    view = hv.HistoryView(settings)
    window = Gtk.Window()
    window.set_child(view)
    window.present()
    yield view, settings, window
    window.destroy()


def _loaded(view):
    view.reload()
    _pump(lambda: not view._loading and view._stack.get_visible_child_name() != "loading")
    return view


def test_shows_every_entry_with_its_details(page, tmp_path):
    view, _s, _w = page
    _add("Primeira leitura", "rhvoice", tmp=tmp_path)
    _add("Segunda leitura", "kokoro", audio=False, tmp=tmp_path)
    _loaded(view)
    assert view._stack.get_visible_child_name() == "content"
    assert [c.entry["text"] for c in view._cards] == ["Segunda leitura", "Primeira leitura"]
    with_audio = [c for c in view._cards if c._player is not None]
    assert [c.entry["text"] for c in with_audio] == ["Primeira leitura"]
    assert with_audio[0]._player._pipeline is None  # GStreamer only on play


def test_filters_by_engine_date_and_text(page, tmp_path):
    view, _s, _w = page
    _add("hoje piper", "piper", tmp=tmp_path)
    _add("ontem kokoro", "kokoro", days_ago=1, tmp=tmp_path)
    _add("antigo rhvoice", "rhvoice", days_ago=20, tmp=tmp_path)
    _loaded(view)
    assert len(view._cards) == 3
    view._period.set_selected(2)  # Yesterday
    _pump(lambda: [c.entry["text"] for c in view._cards] == ["ontem kokoro"])
    view._period.set_selected(0)
    view._engine.set_selected(view._backends.index("rhvoice") + 1)
    _pump(lambda: [c.entry["text"] for c in view._cards] == ["antigo rhvoice"])
    view._engine.set_selected(0)
    view._search.set_text("piper")
    assert _pump(lambda: [c.entry["text"] for c in view._cards] == ["hoje piper"], timeout=3)


def test_loads_more_at_the_bottom(page, tmp_path, monkeypatch):
    monkeypatch.setattr(hv, "PAGE_SIZE", 5)
    view, _s, _w = page
    for i in range(12):
        _add(f"leitura {i}", audio=False, tmp=tmp_path)
    _loaded(view)
    assert len(view._cards) == 5 and view._total == 12
    view._on_edge_reached(view._scroll, Gtk.PositionType.BOTTOM)
    assert _pump(lambda: len(view._cards) == 10)


def test_empty_states_explain_what_to_do(page, tmp_path):
    view, settings, _w = page
    _loaded(view)
    assert view._stack.get_visible_child_name() == "empty"
    assert "tts-biglinux" in view._empty.get_description()  # says where it is kept
    settings.history.enabled = False
    view.on_enable_history = lambda: None
    view.set_history_enabled(False)
    assert view._banner.get_revealed()
    assert view._empty_button.get_visible()  # "Turn On History"
    _add("algo", tmp=tmp_path)
    view._search.set_text("nada disso")
    _pump(lambda: view._stack.get_visible_child_name() == "empty" and view._has_filters(), timeout=3)
    assert view._empty_button.get_label() == hv._("Clear Filters")


def test_saving_off_keeps_the_page_and_the_old_readings(page, tmp_path):
    view, settings, _w = page
    _add("antes de desligar", tmp=tmp_path)
    settings.history.enabled = False
    view.set_history_enabled(False)
    _loaded(view)
    assert [c.entry["text"] for c in view._cards] == ["antes de desligar"]
    assert view._banner.get_revealed()


def test_new_readings_appear_by_themselves(page, tmp_path):
    view, _s, _w = page
    _loaded(view)
    _add("chegou agora", tmp=tmp_path)
    assert _pump(lambda: [c.entry["text"] for c in view._cards] == ["chegou agora"])


def test_delete_can_be_undone(page, tmp_path):
    view, _s, _w = page
    _add("não apague", tmp=tmp_path)
    _loaded(view)
    card = view._cards[0]
    view.delete_with_undo(card)
    assert not card.get_parent().get_visible()  # hidden at once
    view._undo_toast.emit("button-clicked")
    view._undo_toast.dismiss()
    _pump(timeout=0.5)
    assert card.get_parent().get_visible()
    assert hs.count_history_entries() == 1


def test_delete_happens_when_the_undo_toast_goes_away(page, tmp_path):
    view, _s, _w = page
    _add("pode apagar", tmp=tmp_path)
    _loaded(view)
    view.delete_with_undo(view._cards[0])
    assert hs.count_history_entries() == 1  # not yet: Undo is still possible
    view._undo_toast.dismiss()
    assert _pump(lambda: hs.count_history_entries() == 0)
    assert view._cards == []


def test_delete_selected_asks_first(page, tmp_path, monkeypatch):
    view, _s, _w = page
    deleted = []
    monkeypatch.setattr(hs, "delete_entries", lambda ids: deleted.extend(ids) or len(ids))
    presented = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda dialog, parent: presented.append(dialog))
    _add("um", tmp=tmp_path)
    _add("dois", tmp=tmp_path)
    _loaded(view)
    view._select_toggle.set_active(True)
    view._select_all(True)
    view._on_delete_selected(None)
    assert presented and deleted == []  # nothing deleted before the answer
    presented[0].emit("response", "cancel")
    assert deleted == [] and len(view._cards) == 2
    presented[0].emit("response", "delete")
    assert _pump(lambda: len(deleted) == 2)
    assert view._cards == []


def test_history_is_always_in_the_main_menu(monkeypatch):
    win_mod = importlib.import_module("window")
    menu = win_mod.TTSWindow._build_menu_model(type("W", (), {"settings": config.AppSettings()})())
    labels = []
    for i in range(menu.get_n_items()):
        section = menu.get_item_link(i, "section")
        for j in range(section.get_n_items()):
            labels.append(section.get_item_attribute_value(j, "label", None).get_string())
    assert hv._("History") in labels


def _child_boxes(flowbox):
    boxes, child = [], flowbox.get_first_child()
    while child:
        ok, rect = child.compute_bounds(flowbox)
        boxes.append((round(rect.get_x()), round(rect.get_y()), round(rect.get_width())))
        child = child.get_next_sibling()
    return boxes


def test_grid_puts_cards_side_by_side_in_equal_columns(page, tmp_path):
    view, _settings, window = page
    window.set_default_size(1000, 700)
    for i in range(4):
        _add("Um texto bem mais longo, para ocupar várias linhas no cartão. " * (i + 1), tmp=tmp_path)
    _loaded(view)
    view._view_toggle.set_active(True)
    _pump(lambda: len(_child_boxes(view._grid)) == 4 and _child_boxes(view._grid)[1][0] > 0)
    boxes = _child_boxes(view._grid)
    assert view._grid.get_visible() and not view._list.get_visible()
    assert boxes[0][1] == boxes[1][1] and boxes[1][0] > boxes[0][0]  # same row, side by side
    assert abs(boxes[0][2] - boxes[1][2]) <= 2  # whatever the text length
    assert view._clamp.get_maximum_size() > 760  # the grid may use the window's width
    view._view_toggle.set_active(False)
    _pump(lambda: view._list.get_first_child() is not None)
    assert view._list.get_visible() and not view._grid.get_visible()
    assert view._clamp.get_maximum_size() == 760


def test_grid_is_one_column_on_a_narrow_window(page, tmp_path):
    view, _settings, window = page
    window.set_default_size(400, 700)
    for i in range(2):
        _add(f"Leitura {i}.", tmp=tmp_path)
    _loaded(view)
    view._view_toggle.set_active(True)
    _pump(lambda: len(_child_boxes(view._grid)) == 2 and _child_boxes(view._grid)[1][1] > 0)
    first, second = _child_boxes(view._grid)
    assert second[1] > first[1] and second[0] == first[0]  # one under the other
