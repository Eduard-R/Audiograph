"""Integration tests for the App orchestrator.

Mocks the heavy collaborators (Transcriber, HotkeyListener, TrayIcon) but
uses the real StateMachine and a real (fake-backed) AudioRecorder. Verifies
end-to-end state transitions for the happy path, empty-audio path, and
error paths, plus the F12/max-reached race resolution.
"""

from __future__ import annotations

import threading
import time
from typing import Callable
from unittest.mock import MagicMock

import numpy as np
import pytest

from diktattool import app as app_mod
from diktattool import recorder as recorder_mod
from diktattool.app import App
from diktattool.config import Config
from diktattool.state import Event, State


class FakeStream:
    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self): self.started = True
    def stop(self): self.stopped = True
    def close(self): self.closed = True

    def push(self, samples: np.ndarray):
        if samples.ndim == 1:
            samples = samples.reshape(-1, 1)
        self.callback(samples, len(samples), None, None)


@pytest.fixture
def fake_stream(monkeypatch):
    created: list[FakeStream] = []
    monkeypatch.setattr(recorder_mod.sd, "InputStream",
                        lambda **kw: created.append(FakeStream(**kw)) or created[-1])
    return created


@pytest.fixture
def app(monkeypatch, fake_stream):
    """App with stubbed hotkey/tray/transcriber/inserter — no real I/O."""
    # Skip TrayIcon.__init__ entirely (pystray tries to build an Icon).
    monkeypatch.setattr(app_mod.TrayIcon, "__init__",
                        lambda self, on_quit, on_open_log=None, on_restart=None, get_tooltip=None: None)
    monkeypatch.setattr(app_mod.TrayIcon, "run", lambda self: None)
    monkeypatch.setattr(app_mod.TrayIcon, "set_status",
                        lambda self, status, tooltip_suffix=None: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(app_mod.HotkeyListener, "stop", lambda self: None)

    # Real Transcriber replaced with a mock that returns a canned text.
    monkeypatch.setattr(app_mod, "Transcriber", MagicMock(return_value=MagicMock(
        load=lambda: None,
        transcribe=lambda audio: " Hallo Welt.",
    )))

    inserted: list[str] = []
    monkeypatch.setattr(app_mod, "insert_text", lambda t: inserted.append(t))

    cfg = Config(max_recording_seconds=1, samplerate=16000)
    a = App(cfg)
    a._inserted = inserted            # test-only shortcut for assertions
    a._start_worker()
    # Prime the state machine to IDLE (skip preload thread).
    a.sm.handle(Event.MODEL_LOADED)
    yield a
    a.shutdown()


def _wait_for_state(sm, want: State, timeout: float = 2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if sm.state is want:
            return
        time.sleep(0.01)
    raise AssertionError(f"state {sm.state} did not reach {want} within {timeout}s")


def test_full_dictation_cycle(app, fake_stream):
    app._on_toggle()                                     # F12 start
    assert app.sm.state is State.RECORDING
    fake_stream[0].push(np.full(4000, 0.1, dtype=np.float32))
    app._on_toggle()                                     # F12 stop

    _wait_for_state(app.sm, State.IDLE)
    assert app._inserted == [" Hallo Welt."]


def test_empty_recording_short_circuits(app, fake_stream):
    app._on_toggle()
    # No audio pushed.
    app._on_toggle()
    _wait_for_state(app.sm, State.IDLE)
    # Empty audio ⇒ transcriber not called, nothing inserted.
    assert app._inserted == []


def test_transcription_error_bounces_to_idle(app, fake_stream, monkeypatch):
    def boom(audio): raise RuntimeError("cuda oom")
    app.transcriber.transcribe = boom

    app._on_toggle()
    fake_stream[0].push(np.full(4000, 0.1, dtype=np.float32))
    app._on_toggle()

    _wait_for_state(app.sm, State.IDLE)
    assert app.sm.last_error is not None
    assert "cuda oom" in app.sm.last_error
    assert app._inserted == []


def test_insert_error_bounces_to_idle(app, fake_stream, monkeypatch):
    def boom(t): raise RuntimeError("clipboard busy")
    monkeypatch.setattr(app_mod, "insert_text", boom)

    app._on_toggle()
    fake_stream[0].push(np.full(4000, 0.1, dtype=np.float32))
    app._on_toggle()

    _wait_for_state(app.sm, State.IDLE)
    assert "clipboard busy" in (app.sm.last_error or "")


def test_toggle_ignored_while_transcribing(app, fake_stream, monkeypatch):
    """A slow transcription must not accept a second F12 as a start-record."""
    barrier = threading.Event()
    release = threading.Event()

    def slow(audio):
        barrier.set()
        release.wait(timeout=2.0)
        return " ok"

    app.transcriber.transcribe = slow

    app._on_toggle()
    fake_stream[0].push(np.full(4000, 0.1, dtype=np.float32))
    app._on_toggle()

    assert barrier.wait(1.0)
    # We are now inside transcribe. Fire F12 — it must be ignored.
    assert app.sm.state is State.TRANSCRIBING
    app._on_toggle()
    assert app.sm.state is State.TRANSCRIBING

    release.set()
    _wait_for_state(app.sm, State.IDLE)


def test_mic_start_failure_flashes_error(app, monkeypatch):
    def boom():
        raise OSError("no mic")
    monkeypatch.setattr(app.recorder, "start", boom)

    app._on_toggle()
    # We never left IDLE.
    assert app.sm.state is State.IDLE
    assert "no mic" in (app.sm.last_error or "")


def test_restart_spawns_new_instance_then_shuts_down(app, monkeypatch):
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(app_mod.subprocess, "Popen",
                        lambda cmd, **kw: calls.append((cmd, kw)) or MagicMock())

    app.restart()

    # Spawned exactly one detached child pointing at `-m diktattool`.
    assert len(calls) == 1
    cmd, kw = calls[0]
    assert cmd[-2:] == ["-m", "diktattool"]
    assert kw.get("close_fds") is True
    # And the current app is now shutting down.
    assert app._shutting_down.is_set()


def test_restart_is_idempotent(app, monkeypatch):
    """Double-click on 'Neustart' must not spawn two children."""
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(app_mod.subprocess, "Popen",
                        lambda cmd, **kw: calls.append((cmd, kw)) or MagicMock())

    app.restart()
    app.restart()
    assert len(calls) == 1


def test_restart_still_shuts_down_if_spawn_fails(app, monkeypatch):
    """A failing Popen must not leave the tray icon stuck alive."""
    def boom(cmd, **kw):
        raise OSError("cannot spawn")
    monkeypatch.setattr(app_mod.subprocess, "Popen", boom)

    app.restart()

    assert app._shutting_down.is_set()


def test_finalize_race_only_one_wins(app, fake_stream):
    """Both F12 and the max-reached timer try to finalize the same recording.
    The lock guarantees exactly one succeeds; the other bails out on the
    state check."""
    app._on_toggle()
    fake_stream[0].push(np.full(4000, 0.1, dtype=np.float32))

    # Directly invoke both handlers as if they raced.
    barrier = threading.Barrier(2)
    def call_toggle():
        barrier.wait()
        app._on_toggle()
    def call_max():
        barrier.wait()
        app._on_max_reached()

    t1 = threading.Thread(target=call_toggle)
    t2 = threading.Thread(target=call_max)
    t1.start(); t2.start()
    t1.join(); t2.join()

    _wait_for_state(app.sm, State.IDLE)
    # Exactly one insert reached the sink — no duplicate transitions.
    assert app._inserted == [" Hallo Welt."]
