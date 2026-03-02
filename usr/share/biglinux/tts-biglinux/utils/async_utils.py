"""Async utilities: debouncing, threading helpers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

logger = logging.getLogger(__name__)


class Debouncer:
    """Debounce calls to a function — only the last call within the delay fires."""

    def __init__(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self._delay_ms = delay_ms
        self._callback = callback
        self._timer_id: int = 0

    def trigger(self) -> None:
        """Schedule the callback, cancelling any pending invocation."""
        if self._timer_id:
            GLib.source_remove(self._timer_id)
        self._timer_id = GLib.timeout_add(self._delay_ms, self._fire)

    def _fire(self) -> bool:
        self._timer_id = 0
        self._callback()
        return GLib.SOURCE_REMOVE

    def cancel(self) -> None:
        """Cancel any pending invocation."""
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0


def run_in_thread(
    target: Callable[..., Any],
    *args: Any,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
) -> threading.Thread:
    """
    Run a function in a background thread, delivering result to main thread.

    Args:
        target: Function to run in background
        on_done: Called on main thread with result
        on_error: Called on main thread with exception
    """

    def _worker() -> None:
        try:
            result = target(*args)
            if on_done:
                GLib.idle_add(on_done, result)
        except Exception as e:
            logger.exception("Thread error in %s", target.__name__)
            if on_error:
                GLib.idle_add(on_error, e)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread
