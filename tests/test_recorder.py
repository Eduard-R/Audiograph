"""Unit tests for AudioRecorder.

`sounddevice` is mocked — no real audio device is opened. We use a fake
``InputStream`` that exposes the same ``callback`` the recorder registered,
so tests can push frames synchronously and observe the buffer.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np
import pytest

from diktattool import recorder as recorder_mod
from diktattool.recorder import AudioRecorder


class FakeStream:
    """Stand-in for ``sounddevice.InputStream``.

    Captures the callback so the test can drive it, and records the calls
    the recorder makes on it (start / stop / close)."""

    def __init__(self, *, samplerate: int, channels: int, dtype: str,
                 callback: Callable, blocksize: int = 0):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.callback = callback
        self.blocksize = blocksize
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    # Feed helper used by tests.
    def push(self, samples: np.ndarray):
        # Match the sounddevice API: 2-D array (frames, channels).
        if samples.ndim == 1:
            samples = samples.reshape(-1, 1)
        self.callback(samples, len(samples), None, None)


@pytest.fixture
def fake_stream(monkeypatch):
    """Patch ``sounddevice.InputStream`` and hand tests the created instance."""
    created: list[FakeStream] = []

    def factory(**kwargs):
        s = FakeStream(**kwargs)
        created.append(s)
        return s

    monkeypatch.setattr(recorder_mod.sd, "InputStream", factory)
    return created


def test_start_opens_stream_at_16k_mono(fake_stream):
    r = AudioRecorder()
    r.start()
    assert len(fake_stream) == 1
    s = fake_stream[0]
    assert s.samplerate == 16000
    assert s.channels == 1
    assert s.started is True
    assert r.is_recording is True


def test_stop_returns_captured_audio(fake_stream):
    r = AudioRecorder()
    r.start()
    s = fake_stream[0]
    s.push(np.full(8000, 0.25, dtype=np.float32))
    s.push(np.full(4000, -0.25, dtype=np.float32))

    audio = r.stop()

    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert audio.shape == (12000,)
    assert audio[0] == pytest.approx(0.25)
    assert audio[-1] == pytest.approx(-0.25)
    assert s.stopped is True
    assert s.closed is True
    assert r.is_recording is False


def test_stop_without_start_returns_empty_array(fake_stream):
    r = AudioRecorder()
    audio = r.stop()
    assert audio.shape == (0,)


def test_max_reached_fires_notification(fake_stream):
    """Contract: the timer fires the callback but does NOT stop the mic —
    the caller (App) is responsible for calling stop() to drain the audio.
    Stopping in here would discard the very audio the user just spoke."""
    triggered = threading.Event()
    r = AudioRecorder(max_seconds=1, on_max_reached=lambda: triggered.set())
    r.start()

    fake_stream[0].push(np.full(4000, 0.1, dtype=np.float32))
    assert triggered.wait(timeout=3.0), "on_max_reached did not fire"
    assert r.is_recording is True, "recorder must not self-stop on max_reached"

    # Caller drains the audio in reaction to the callback.
    audio = r.stop()
    assert audio.shape == (4000,)


def test_max_reached_only_fires_once_per_start(fake_stream, monkeypatch):
    calls: list[int] = []
    r = AudioRecorder(max_seconds=1, on_max_reached=lambda: calls.append(1))
    r.start()
    time.sleep(1.4)
    r.stop()
    time.sleep(0.2)
    assert calls == [1]


def test_second_start_after_stop_works(fake_stream):
    r = AudioRecorder()
    r.start()
    fake_stream[0].push(np.zeros(1000, dtype=np.float32))
    r.stop()
    r.start()
    assert len(fake_stream) == 2
    assert r.is_recording is True
    r.stop()


def test_stream_open_failure_leaves_recorder_stopped(monkeypatch):
    def boom(**kwargs):
        raise OSError("no mic")

    monkeypatch.setattr(recorder_mod.sd, "InputStream", boom)
    r = AudioRecorder()
    with pytest.raises(OSError):
        r.start()
    assert r.is_recording is False


def test_multichannel_input_downmixed_to_mono(fake_stream):
    """Some drivers deliver stereo even when we asked for mono; we accept
    that and take channel 0 rather than crashing."""
    r = AudioRecorder()
    r.start()
    s = fake_stream[0]
    stereo = np.column_stack([
        np.full(1000, 0.5, dtype=np.float32),
        np.full(1000, -0.5, dtype=np.float32),
    ])
    s.callback(stereo, len(stereo), None, None)
    audio = r.stop()
    assert audio.shape == (1000,)
    assert audio[0] == pytest.approx(0.5)


def test_falls_back_to_device_native_rate_when_target_refused(monkeypatch):
    """Windows MME / Bluetooth mics refuse 16 kHz — we must reopen the
    stream at the device's native rate rather than dying."""
    created: list[FakeStream] = []

    def factory(**kwargs):
        if kwargs["samplerate"] == 16000:
            raise recorder_mod.sd.PortAudioError("Invalid sample rate", -9997)
        s = FakeStream(**kwargs)
        created.append(s)
        return s

    monkeypatch.setattr(recorder_mod.sd, "InputStream", factory)
    monkeypatch.setattr(
        recorder_mod.sd, "query_devices",
        lambda device=None, kind=None: {"default_samplerate": 44100.0},
    )

    r = AudioRecorder()
    r.start()
    assert r.is_recording is True
    assert len(created) == 1, "recorder should have opened exactly one stream"
    assert created[0].samplerate == 44100
    assert created[0].started is True


def test_stop_resamples_captured_audio_to_target_rate(monkeypatch):
    """When we had to capture at a non-target rate, stop() must resample
    to self._samplerate so downstream (Whisper) still gets 16 kHz."""
    created: list[FakeStream] = []

    def factory(**kwargs):
        if kwargs["samplerate"] == 16000:
            raise recorder_mod.sd.PortAudioError("Invalid sample rate", -9997)
        s = FakeStream(**kwargs)
        created.append(s)
        return s

    monkeypatch.setattr(recorder_mod.sd, "InputStream", factory)
    monkeypatch.setattr(
        recorder_mod.sd, "query_devices",
        lambda device=None, kind=None: {"default_samplerate": 48000.0},
    )

    r = AudioRecorder()
    r.start()
    s = created[0]
    # Push exactly one second of audio at 48 kHz.
    s.push(np.full(48000, 0.5, dtype=np.float32))
    audio = r.stop()

    assert audio.dtype == np.float32
    assert audio.ndim == 1
    # 48000 samples @ 48 kHz = 1.0 s → expect ~16000 samples @ 16 kHz.
    assert 15990 <= audio.shape[0] <= 16010
    # Constant input → constant output (modulo tiny endpoint effects).
    assert np.allclose(audio, 0.5, atol=1e-5)


def test_fallback_reraises_original_error_when_query_devices_fails(monkeypatch):
    """If we can't discover a native rate, propagate the sample-rate error
    as-is rather than masking it with a query_devices failure."""
    def factory(**kwargs):
        raise recorder_mod.sd.PortAudioError("Invalid sample rate", -9997)

    def broken_query(*args, **kwargs):
        raise RuntimeError("no such device")

    monkeypatch.setattr(recorder_mod.sd, "InputStream", factory)
    monkeypatch.setattr(recorder_mod.sd, "query_devices", broken_query)

    r = AudioRecorder()
    with pytest.raises(recorder_mod.sd.PortAudioError):
        r.start()
    assert r.is_recording is False


def test_non_portaudio_errors_still_propagate(monkeypatch):
    """The fallback must only catch PortAudioError. Other exceptions
    (e.g. OSError from a broken device) must not be swallowed."""
    def boom(**kwargs):
        raise OSError("no mic")

    monkeypatch.setattr(recorder_mod.sd, "InputStream", boom)
    r = AudioRecorder()
    with pytest.raises(OSError):
        r.start()
    assert r.is_recording is False


def test_resample_linear_helper():
    """Sanity-check the resampler in isolation."""
    from diktattool.recorder import _resample_linear

    # Identity.
    x = np.arange(10, dtype=np.float32)
    out = _resample_linear(x, 16000, 16000)
    assert out is x or np.array_equal(out, x)

    # Empty stays empty.
    empty = np.zeros(0, dtype=np.float32)
    assert _resample_linear(empty, 44100, 16000).shape == (0,)

    # 3:1 downsample of a constant is a constant with ~1/3 the length.
    src = np.full(3000, 0.7, dtype=np.float32)
    out = _resample_linear(src, 48000, 16000)
    assert 999 <= out.shape[0] <= 1001
    assert np.allclose(out, 0.7, atol=1e-6)
