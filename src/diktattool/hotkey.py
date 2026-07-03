"""Global hotkey listener wrapping the ``keyboard`` library.

We register a single hotkey. The library fires our callback from an
internal thread; we debounce by 200 ms because Windows key-repeat can
deliver duplicate press events for a held F12 and the resulting
double-toggle is very confusing (start-and-immediately-stop).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import keyboard


logger = logging.getLogger(__name__)


class HotkeyListener:
    """Register / unregister a global hotkey. Not reusable after stop()."""

    DEBOUNCE_SECONDS = 0.2

    def __init__(self, key: str, on_toggle: Callable[[], None]) -> None:
        self._key = key
        self._on_toggle = on_toggle
        self._lock = threading.Lock()
        self._last_fire_monotonic: float = float("-inf")
        self._hook_handle = None
        self._monotonic = time.monotonic

    def start(self) -> None:
        if self._hook_handle is not None:
            raise RuntimeError("HotkeyListener already started")
        # `add_hotkey` returns a handle we later hand to `remove_hotkey`.
        # `suppress=False` so the key still reaches focused apps as usual —
        # F12 is unbound in most tools, but if the user changes hotkey to
        # something that has meaning, we don't want to swallow it.
        self._hook_handle = keyboard.add_hotkey(self._key, self._fire, suppress=False)
        logger.info("hotkey: bound %s", self._key)

    def stop(self) -> None:
        with self._lock:
            handle = self._hook_handle
            self._hook_handle = None
        if handle is not None:
            try:
                keyboard.remove_hotkey(handle)
            except (KeyError, ValueError):  # pragma: no cover
                pass
            logger.info("hotkey: unbound")

    def _fire(self) -> None:
        now = self._monotonic()
        with self._lock:
            if now - self._last_fire_monotonic < self.DEBOUNCE_SECONDS:
                logger.debug("hotkey: debounced (%.3fs)",
                             now - self._last_fire_monotonic)
                return
            self._last_fire_monotonic = now
        try:
            self._on_toggle()
        except Exception:  # pragma: no cover
            logger.exception("hotkey: on_toggle raised")
