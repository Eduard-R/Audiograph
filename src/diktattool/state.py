"""Dictation state machine.

Six states with deterministic transitions (see design §4.2). The machine
itself is I/O-free and thread-safe under a single ``threading.Lock``; the
orchestrator in :mod:`diktattool.app` drives it by feeding events.
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Callable


logger = logging.getLogger(__name__)


class State(Enum):
    LOADING = "loading"
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    INSERTING = "inserting"


class Event(Enum):
    MODEL_LOADED = "model_loaded"
    MODEL_LOAD_FAILED = "model_load_failed"
    TOGGLE = "toggle"                    # F12 pressed
    MAX_REACHED = "max_reached"          # recorder auto-stopped
    TRANSCRIPTION_DONE = "transcription_done"
    INSERT_DONE = "insert_done"
    ERROR = "error"


class IllegalTransition(RuntimeError):
    """Raised when an event is fired in a state where it cannot possibly be
    valid — this is a bug in the caller, not a runtime condition."""


TransitionHook = Callable[[State, State], None]


class StateMachine:
    """Thread-safe state machine. Any event may be posted from any thread; a
    single internal lock serializes the transition itself. The optional
    :attr:`on_transition` hook runs *inside* the lock, so keep it fast — the
    orchestrator uses it to update the tray icon and emit log lines."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = State.LOADING
        self._model_load_failed = False
        self._last_error: str | None = None
        self.on_transition: TransitionHook | None = None

    # -- read-only views ---------------------------------------------------

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    @property
    def model_load_failed(self) -> bool:
        with self._lock:
            return self._model_load_failed

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    # -- test helper -------------------------------------------------------

    def force_state(self, s: State) -> None:
        """Test-only: jump into ``s`` without running transition logic."""
        with self._lock:
            self._state = s

    # -- event handling ----------------------------------------------------

    def handle(self, event: Event, *, message: str | None = None) -> None:
        """Apply ``event``. May be a no-op (e.g. TOGGLE while busy)."""
        with self._lock:
            new_state = self._next_state(event, message)
            if new_state is None:
                return
            old_state = self._state
            if new_state is old_state:
                return
            self._state = new_state
            hook = self.on_transition
        # Run hook OUTSIDE the lock so it can safely call back into the SM
        # if needed (e.g. logging that dispatches another event).
        if hook is not None:
            try:
                hook(old_state, new_state)
            except Exception:  # pragma: no cover
                logger.exception("state: on_transition hook raised")

    # -- transition table --------------------------------------------------

    def _next_state(self, event: Event, message: str | None) -> State | None:
        """Return the target state, or ``None`` for no-ops."""
        s = self._state

        if event is Event.MODEL_LOADED:
            if self._model_load_failed:
                return None
            if s is State.LOADING:
                return State.IDLE
            return None

        if event is Event.MODEL_LOAD_FAILED:
            self._model_load_failed = True
            self._last_error = message
            return None  # stay in LOADING permanently

        if event is Event.ERROR:
            self._last_error = message
            if s is State.LOADING:
                # Only relevant while we're still loading; treat like a fatal
                # load failure so the tray goes red-blinking permanently.
                self._model_load_failed = True
                return None
            return State.IDLE

        if self._model_load_failed:
            return None  # everything else ignored while model is unusable

        if event is Event.TOGGLE:
            if s is State.IDLE:
                return State.RECORDING
            if s is State.RECORDING:
                return State.TRANSCRIBING
            return None

        if event is Event.MAX_REACHED:
            if s is State.RECORDING:
                return State.TRANSCRIBING
            return None

        if event is Event.TRANSCRIPTION_DONE:
            if s is State.TRANSCRIBING:
                return State.INSERTING
            raise IllegalTransition(f"TRANSCRIPTION_DONE fired in {s}")

        if event is Event.INSERT_DONE:
            if s is State.INSERTING:
                self._last_error = None  # a successful round clears the flag
                return State.IDLE
            raise IllegalTransition(f"INSERT_DONE fired in {s}")

        raise IllegalTransition(f"unhandled event {event}")  # pragma: no cover
