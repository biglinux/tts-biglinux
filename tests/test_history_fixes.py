"""Regressions of the History page review: double cards, Undo after a reload,
queue playback, leaked bus watches, a frozen "Open file location", the
JSON migration race and the player's accessible names.

GTK tests are skipped without a display — run headless with
GDK_BACKEND=broadway (see test_history_view.py).
"""
import importlib
import json
import threading
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gst, Gtk  # noqa: E402

HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
needs_gtk = pytest.mark.skipif(not HAVE_DISPLAY, reason="no display for GTK")
if HAVE_DISPLAY:
    Adw.init()

from fake_engines import text_wav  # noqa: E402

config = importlib.import_module("config")
hs = importlib.import_module("services.history_service")
hdb = importlib.import_module("services.history_db")
hv = importlib.import_module("ui.history_view") if HAVE_DISPLAY else None
ap = importlib.import_module("ui.audio_player") if HAVE_DISPLAY else None


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


def _add(text, backend="piper", audio=True, tmp=None):
    path = text_wav(tmp / f"{abs(hash(text))}.wav", "a") if audio else None
    hs.save_history_entry(text=text, audio_path=path, backend=backend, voice_id=f"{backend}:voz")


def _settled(view):
    """Wait until no page is loading, then a little more (late reloads)."""
    _pump(lambda: not view._loading and view._stack.get_visible_child_name() != "loading")
    _pump(timeout=0.3)
    _pump(lambda: not view._loading)
    return view


@pytest.fixture
def page(tmp_path):
    settings = config.AppSettings()
    view = hv.HistoryView(settings)
    window = Gtk.Window()
    window.set_child(view)
    window.present()
    yield view
    window.destroy()


def _ids(view):
    return [c.entry["id"] for c in view._cards]


def _texts(view):
    return [c.entry["text"] for c in view._cards]


# ── 1. A new engine in the filter must not load every card twice ───────

@needs_gtk
def test_new_engine_keeps_the_filter_and_loads_cards_once(page, tmp_path):
    view = page
    _add("leitura kokoro", "kokoro", tmp=tmp_path)
    _add("leitura piper", "piper", tmp=tmp_path)
    view.reload()
    _settled(view)
    assert view._backends == ["kokoro", "piper"]
    view._engine.set_selected(2)  # piper
    _settled(view)
    assert _texts(view) == ["leitura piper"]

    # "espeak" sorts first: piper moves from position 2 to 3 in the model.
    _add("leitura espeak", "espeak", tmp=tmp_path)
    view.reload()
    _settled(view)
    assert view._backends == ["espeak", "kokoro", "piper"]
    assert view._engine.get_selected() == 3
    assert view._filters()["backend"] == "piper"
    assert _texts(view) == ["leitura piper"]
    assert len(set(_ids(view))) == len(_ids(view))
    assert view._offset == 1


@needs_gtk
def test_engine_gone_resets_the_filter_once_without_duplicates(page, tmp_path):
    view = page
    _add("leitura kokoro", "kokoro", tmp=tmp_path)
    _add("leitura piper", "piper", tmp=tmp_path)
    view.reload()
    _settled(view)
    view._engine.set_selected(1)  # kokoro
    _settled(view)
    assert _texts(view) == ["leitura kokoro"]
    with hdb._connect() as conn:
        conn.execute("DELETE FROM entries WHERE backend = 'kokoro'")
    view.reload()
    _settled(view)
    assert view._engine.get_selected() == 0
    assert _texts(view) == ["leitura piper"]
    assert view._offset == 1


# ── 2. Undo / delete after the list was reloaded ───────────────────────

@needs_gtk
def test_undo_after_a_reload_brings_the_entry_back(page, tmp_path):
    view = page
    _add("fica", tmp=tmp_path)
    _add("quase apagada", tmp=tmp_path)
    view.reload()
    _settled(view)
    card = next(c for c in view._cards if c.entry["text"] == "quase apagada")
    entry_id = card.entry["id"]
    view.delete_with_undo(card)
    view.reload()  # e.g. a reading was saved meanwhile
    _settled(view)
    new = view._pending_delete[entry_id]
    assert new is not card and new in view._cards
    assert not new.get_parent().get_visible()  # still hidden
    view._undo_toast.emit("button-clicked")
    view._undo_toast.dismiss()
    _pump(timeout=0.3)
    assert new.get_parent().get_visible()
    assert entry_id not in view._pending_delete
    assert hs.count_history_entries() == 2


@needs_gtk
def test_delete_after_a_reload_removes_only_real_children(page, tmp_path):
    view = page
    _add("apagar depois", tmp=tmp_path)
    _add("outra", tmp=tmp_path)
    view.reload()
    _settled(view)
    card = next(c for c in view._cards if c.entry["text"] == "apagar depois")
    old_row = card.get_parent()
    view.delete_with_undo(card)
    view.reload()
    _settled(view)
    assert old_row.get_parent() is None  # the reload dropped the old row

    removed = []
    real_remove = view._list.remove

    def checked_remove(row):
        assert row.get_parent() is view._list, "removing a row that is not in the list"
        removed.append(row)
        real_remove(row)

    view._list.remove = checked_remove
    view._undo_toast.dismiss()
    assert _pump(lambda: hs.count_history_entries() == 1)
    assert len(removed) == 1
    _settled(view)  # deleting notifies the listeners: the page reloads
    assert _texts(view) == ["outra"]

    # The original card (already out of the list) is handled quietly too.
    view._remove_card(card)
    assert len(removed) == 1


@needs_gtk
def test_deleting_the_only_card_after_a_reload_shows_the_empty_page(page, tmp_path):
    view = page
    _add("única", tmp=tmp_path)
    view.reload()
    _settled(view)
    view.delete_with_undo(view._cards[0])
    view.reload()
    _settled(view)
    assert view._stack.get_visible_child_name() == "empty"  # only a hidden card
    view._undo_toast.emit("button-clicked")
    view._undo_toast.dismiss()
    _pump(timeout=0.3)
    assert view._stack.get_visible_child_name() == "content"
    assert view._cards[0].get_parent().get_visible()


# ── 3. Queue mode ──────────────────────────────────────────────────────

class _FakePipeline:
    def __init__(self):
        self.states = []

    def set_state(self, state):
        self.states.append(state)

    def query_position(self, _fmt):
        return False, 0

    def query_duration(self, _fmt):
        return False, 0


@pytest.fixture
def players():
    cls = ap.AudioPlayerWidget
    made = []

    def make():
        p = cls("/nonexistent.wav")
        p._pipeline = _FakePipeline()
        made.append(p)
        return p

    cls.set_playback_mode("queue")
    yield make
    for p in made:
        p.cleanup()
    cls.set_playback_mode("interrupt")
    cls._queue.clear()
    cls._active_player = None


@needs_gtk
def test_queue_advances_when_the_current_one_is_stopped(players):
    cls = ap.AudioPlayerWidget
    a, b, c = players(), players(), players()
    a._on_play_pause(None)
    b._on_play_pause(None)
    c._on_play_pause(None)
    assert a._playing and not b._playing and not c._playing
    assert cls._queue == [b, c]
    a._on_stop()
    assert b._playing and cls._active_player is b
    assert cls._queue == [c]
    b._on_eos_idle()  # end of b
    assert c._playing and cls._queue == []
    c._on_eos_idle()
    assert not any(p._playing for p in (a, b, c))
    assert cls._active_player is None


@needs_gtk
def test_queued_player_started_by_hand_does_not_play_again(players):
    cls = ap.AudioPlayerWidget
    a, b = players(), players()
    a._on_play_pause(None)
    b._on_play_pause(None)
    assert cls._queue == [b]
    a._on_play_pause(None)  # pause a: nothing is playing now
    b._on_play_pause(None)  # b starts by hand
    assert b._playing and cls._queue == []
    b._on_eos_idle()
    assert not b._playing  # not replayed from its old place in the queue


@needs_gtk
def test_stopping_in_interrupt_mode_does_not_start_the_queue(players):
    cls = ap.AudioPlayerWidget
    a, b = players(), players()
    a._on_play_pause(None)
    b._on_play_pause(None)
    cls.set_playback_mode("interrupt")
    assert cls._queue == []
    a._on_stop()
    assert not b._playing


# ── 4. The bus watch is released ───────────────────────────────────────

@needs_gtk
def test_cleanup_removes_the_bus_watch(tmp_path, monkeypatch):
    wav = text_wav(tmp_path / "a.wav", "a")
    player = ap.AudioPlayerWidget(str(wav))
    assert player._ensure_pipeline()
    bus = player._bus
    handlers = list(player._bus_handlers)
    assert handlers and all(bus.handler_is_connected(h) for h in handlers)
    removed = []
    real = Gst.Bus.remove_signal_watch
    monkeypatch.setattr(Gst.Bus, "remove_signal_watch", lambda b: (removed.append(b), real(b))[1])
    player.cleanup()
    assert removed == [bus]
    assert not any(bus.handler_is_connected(h) for h in handlers)
    assert player._bus is None and player._pipeline is None
    player.cleanup()  # twice is harmless
    assert removed == [bus]


# ── 5. Opening the folder and saving never block the window ───────────

class _FakeConn:
    def __init__(self, fail):
        self.fail = fail
        self.calls = []

    def call(self, name, path, iface, method, params, reply, flags, timeout, cancel, callback):
        self.calls.append((name, method, params.unpack(), timeout))
        callback(self, "result")

    def call_finish(self, _result):
        if self.fail:
            raise GLib.Error("org.freedesktop.DBus.Error.ServiceUnknown")


@needs_gtk
@pytest.mark.parametrize("fail", [False, True])
def test_open_file_location_is_asynchronous(monkeypatch, tmp_path, fail):
    target = tmp_path / "leitura.wav"
    target.write_bytes(b"")
    pending, opened = [], []
    conn = _FakeConn(fail)
    monkeypatch.setattr(Gio, "bus_get", lambda bus, cancel, cb: pending.append(cb))
    monkeypatch.setattr(Gio, "bus_get_finish", lambda _r: conn)
    monkeypatch.setattr(hv, "_open_folder", opened.append)
    hv._show_in_file_manager(target)
    assert len(pending) == 1 and conn.calls == []  # returned at once, no waiting
    pending[0](None, "result")
    (name, method, params, timeout) = conn.calls[0]
    assert (name, method) == ("org.freedesktop.FileManager1", "ShowItems")
    assert params == ([target.as_uri()], "")
    assert 0 < timeout <= 15000
    assert opened == ([target] if fail else [])


@needs_gtk
def test_open_file_location_without_a_bus_opens_the_folder(monkeypatch, tmp_path):
    opened = []

    def no_bus(_r):
        raise GLib.Error("no session bus")

    monkeypatch.setattr(Gio, "bus_get", lambda bus, cancel, cb: cb(None, "result"))
    monkeypatch.setattr(Gio, "bus_get_finish", no_bus)
    monkeypatch.setattr(hv, "_open_folder", opened.append)
    hv._show_in_file_manager(tmp_path)
    assert opened == [tmp_path]


@needs_gtk
def test_save_audio_copies_off_the_main_thread(page, tmp_path, monkeypatch):
    view = page
    _add("salvar", tmp=tmp_path)
    view.reload()
    _settled(view)
    card = view._cards[0]
    dest = tmp_path / "out.wav"
    threads, toasts = [], []
    monkeypatch.setattr(Gtk.FileDialog, "save", lambda d, parent, cancel, cb: cb(d, None))
    monkeypatch.setattr(Gtk.FileDialog, "save_finish", lambda d, r: Gio.File.new_for_path(str(dest)))
    real_export = hs.export_audio

    def export(entry, destination):
        threads.append(threading.current_thread())
        return real_export(entry, destination)

    monkeypatch.setattr(hs, "export_audio", export)
    view.toast = toasts.append
    card._on_export()
    assert _pump(lambda: toasts)
    assert threads and threads[0] is not threading.main_thread()
    assert toasts == [hv._("Audio saved")]
    assert dest.stat().st_size > 0


# ── 6. Two migrations at once do not duplicate rows ────────────────────

def test_concurrent_json_migrations_insert_each_entry_once(tmp_path, monkeypatch):
    folder = hs.get_history_dir()
    folder.mkdir(parents=True, exist_ok=True)
    entries = [
        {"timestamp": f"2026-09-15_10-00-{i:02d}-000000", "backend": "piper",
         "voice_id": "v", "text_preview": f"t{i}", "has_audio": False}
        for i in range(6)  # no ids: duplicates would get fresh uuids
    ]
    jf = folder / "history.json"
    jf.write_text(json.dumps(entries), encoding="utf-8")
    hdb.count_entries()  # create the database first

    real_epoch = hdb.ts_to_epoch

    def slow_epoch(ts):  # widen the window between "check" and "insert"
        time.sleep(0.03)
        return real_epoch(ts)

    monkeypatch.setattr(hdb, "ts_to_epoch", slow_epoch)
    real_read = type(jf).read_text
    barrier = threading.Barrier(2)

    def read_together(self, *a, **kw):
        text = real_read(self, *a, **kw)
        if self.name == "history.json":
            barrier.wait(timeout=5)  # both runs have the data before either inserts
        return text

    monkeypatch.setattr(type(jf), "read_text", read_together)
    results, errors = [], []

    def run():
        try:
            results.append(hdb.migrate_from_json(jf))
        except Exception as e:  # IntegrityError / duplicates are the bug
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []
    assert sorted(results) == [0, 6]
    assert hdb.count_entries() == 6


# ── 7. Accessible names of the player ─────────────────────────────────

@needs_gtk
def test_player_controls_have_accessible_names(monkeypatch):
    labels = {}
    real = Gtk.Widget.update_property

    def record(widget, props, values):
        for prop, value in zip(props, values):
            if prop == Gtk.AccessibleProperty.LABEL:
                labels[widget] = value
        return real(widget, props, values)

    monkeypatch.setattr(Gtk.Widget, "update_property", record, raising=False)
    player = ap.AudioPlayerWidget("/nonexistent.wav")
    _ = ap._
    assert labels[player._play_btn] == _("Play")
    assert labels[player._stop_btn] == _("Stop")
    assert labels.get(player._seek_scale)
    player._pipeline = _FakePipeline()
    player._on_play_pause(None)
    assert labels[player._play_btn] == _("Pause") == player._play_btn.get_tooltip_text()
    player._on_stop()
    assert labels[player._play_btn] == _("Play")
    player.cleanup()
    ap.AudioPlayerWidget._active_player = None


def test_export_never_leaves_a_truncated_file(tmp_path, monkeypatch):
    import shutil as _shutil

    from services import history_service as hs

    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF" + b"x" * 1000)
    src.chmod(0o644)
    dest = tmp_path / "out" / "saved.wav"
    dest.parent.mkdir()
    dest.write_bytes(b"old")
    monkeypatch.setattr(hs, "audio_path", lambda entry: src)

    def broken_copy(a, b):
        pathlib_b = type(src)(b)
        pathlib_b.write_bytes(b"RI")  # cut short
        raise OSError("disk full")

    real_copy = _shutil.copyfile
    monkeypatch.setattr(_shutil, "copyfile", broken_copy)
    assert hs.export_audio({}, str(dest)) is False
    assert dest.read_bytes() == b"old"
    assert [p.name for p in dest.parent.iterdir()] == ["saved.wav"]

    # (not monkeypatch.undo(): it would also undo conftest's user-data guards)
    monkeypatch.setattr(_shutil, "copyfile", real_copy)
    assert hs.export_audio({}, str(dest)) is True
    assert dest.read_bytes() == src.read_bytes()
    assert dest.stat().st_mode & 0o777 == 0o644


def test_export_keeps_an_existing_files_permissions_and_follows_links(tmp_path, monkeypatch):
    import os

    from services import history_service as hs

    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF" + b"y" * 100)
    src.chmod(0o644)
    monkeypatch.setattr(hs, "audio_path", lambda entry: src)

    private = tmp_path / "private.wav"
    private.write_bytes(b"old")
    private.chmod(0o600)
    assert hs.export_audio({}, str(private))
    assert private.stat().st_mode & 0o777 == 0o600  # never widened

    link = tmp_path / "link.wav"
    os.symlink(private, link)
    assert hs.export_audio({}, str(link))
    assert link.is_symlink() and private.read_bytes() == src.read_bytes()
