"""Unit tests for the dictation state machine.

Follows §4.2 of the design:
    LOADING -> IDLE -> RECORDING -> TRANSCRIBING -> INSERTING -> IDLE
Errors bounce through ERROR back to IDLE (or stay in LOADING for a model-load
failure). F12 is a toggle that starts a recording from IDLE and stops it from
RECORDING; it is ignored in all other states.
"""

import pytest

from diktattool.state import (
    Event,
    IllegalTransition,
    State,
    StateMachine,
)


def test_starts_in_loading():
    sm = StateMachine()
    assert sm.state is State.LOADING


def test_model_loaded_takes_us_to_idle():
    sm = StateMachine()
    sm.handle(Event.MODEL_LOADED)
    assert sm.state is State.IDLE


def test_toggle_from_idle_starts_recording():
    sm = StateMachine()
    sm.handle(Event.MODEL_LOADED)
    sm.handle(Event.TOGGLE)
    assert sm.state is State.RECORDING


def test_toggle_from_recording_stops_and_transcribes():
    sm = StateMachine()
    sm.handle(Event.MODEL_LOADED)
    sm.handle(Event.TOGGLE)  # start recording
    sm.handle(Event.TOGGLE)  # stop
    assert sm.state is State.TRANSCRIBING


def test_max_reached_stops_recording():
    sm = StateMachine()
    sm.handle(Event.MODEL_LOADED)
    sm.handle(Event.TOGGLE)
    sm.handle(Event.MAX_REACHED)
    assert sm.state is State.TRANSCRIBING


def test_full_happy_path():
    sm = StateMachine()
    transitions: list[tuple[State, State]] = []
    sm.on_transition = lambda old, new: transitions.append((old, new))
    sm.handle(Event.MODEL_LOADED)
    sm.handle(Event.TOGGLE)                # IDLE -> RECORDING
    sm.handle(Event.TOGGLE)                # RECORDING -> TRANSCRIBING
    sm.handle(Event.TRANSCRIPTION_DONE)    # TRANSCRIBING -> INSERTING
    sm.handle(Event.INSERT_DONE)           # INSERTING -> IDLE
    assert sm.state is State.IDLE
    assert transitions == [
        (State.LOADING, State.IDLE),
        (State.IDLE, State.RECORDING),
        (State.RECORDING, State.TRANSCRIBING),
        (State.TRANSCRIBING, State.INSERTING),
        (State.INSERTING, State.IDLE),
    ]


@pytest.mark.parametrize("state_before_toggle", [
    State.LOADING,
    State.TRANSCRIBING,
    State.INSERTING,
])
def test_toggle_ignored_in_busy_states(state_before_toggle: State):
    sm = StateMachine()
    sm.force_state(state_before_toggle)
    sm.handle(Event.TOGGLE)
    assert sm.state is state_before_toggle


def test_error_from_recording_returns_to_idle():
    sm = StateMachine()
    sm.handle(Event.MODEL_LOADED)
    sm.handle(Event.TOGGLE)
    sm.handle(Event.ERROR, message="mic gone")
    assert sm.state is State.IDLE
    assert sm.last_error == "mic gone"


def test_error_from_transcribing_returns_to_idle():
    sm = StateMachine()
    sm.force_state(State.TRANSCRIBING)
    sm.handle(Event.ERROR, message="whisper crash")
    assert sm.state is State.IDLE


def test_model_load_failure_keeps_us_in_loading():
    """Design §6: a CUDA/model-load error is fatal-but-alive.
    Tray stays red blinking; state stays LOADING; app is not functional but
    keeps running so the user can see the tray icon and open the log."""
    sm = StateMachine()
    sm.handle(Event.MODEL_LOAD_FAILED, message="CUDA not available")
    assert sm.state is State.LOADING
    assert sm.model_load_failed is True
    assert sm.last_error == "CUDA not available"


def test_toggle_ignored_after_model_load_failure():
    sm = StateMachine()
    sm.handle(Event.MODEL_LOAD_FAILED, message="CUDA not available")
    sm.handle(Event.TOGGLE)
    assert sm.state is State.LOADING


def test_transcription_done_only_valid_in_transcribing():
    sm = StateMachine()
    sm.force_state(State.IDLE)
    with pytest.raises(IllegalTransition):
        sm.handle(Event.TRANSCRIPTION_DONE)


def test_last_error_cleared_on_successful_insert():
    sm = StateMachine()
    sm.handle(Event.MODEL_LOADED)
    sm.handle(Event.TOGGLE)
    sm.handle(Event.ERROR, message="temporary")
    assert sm.last_error == "temporary"
    # Next successful dictation clears the error state.
    sm.handle(Event.TOGGLE)                # IDLE -> RECORDING
    sm.handle(Event.TOGGLE)                # RECORDING -> TRANSCRIBING
    sm.handle(Event.TRANSCRIPTION_DONE)    # -> INSERTING
    sm.handle(Event.INSERT_DONE)           # -> IDLE
    assert sm.last_error is None
