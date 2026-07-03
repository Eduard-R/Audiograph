"""Microphone capture.

Opens ``sounddevice.InputStream`` at 16 kHz mono float32 — the exact format
Whisper wants — and accumulates frames until :meth:`stop` (or an auto-stop
timer fires at ``max_seconds``). Frames are appended to a plain list inside
the audio callback; concatenation only happens on stop, so the callback
stays fast and never allocates a growing buffer.

Some Windows drivers (notably MME and Bluetooth headsets) refuse to open a
stream at 16 kHz — the device only exposes its native shared-mode rate
(commonly 44100 or 48000 Hz). We fall back to that native rate and
resample to 16 kHz on stop, so the app keeps working regardless.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np
import sounddevice as sd  # imported at module top so tests can monkeypatch it


logger = logging.getLogger(__name__)


def _resolve_device(spec: str) -> int | str | None:
    """Turn a config string into what sounddevice wants.

    ``""`` → None (let sounddevice pick the system default).
    ``"3"`` → integer index 3.
    Anything else → the string itself; sounddevice matches by name substring.

    If the system default is broken (device -1 / PortAudioError),
    the caller sees the error and the app flashes red — no auto-recovery here.
    """
    if not spec:
        return None
    try:
        return int(spec)
    except ValueError:
        return spec


def _resample_linear(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample mono float32 audio via linear interpolation.

    No anti-alias filter — good enough for speech fed into Whisper (which
    was trained on real-world 16 kHz audio and tolerates modest resampling
    artefacts). Keeps the runtime dependency-free — no scipy needed.
    """
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    duration = audio.size / src_rate
    new_len = max(1, int(round(duration * dst_rate)))
    old_t = np.arange(audio.size, dtype=np.float64) / src_rate
    new_t = np.arange(new_len, dtype=np.float64) / dst_rate
    return np.interp(new_t, old_t, audio).astype(np.float32, copy=False)


class AudioRecorder:
    def __init__(
        self,
        samplerate: int = 16000,
        max_seconds: int = 120,
        on_max_reached: Callable[[], None] | None = None,
        input_device: str = "",
    ) -> None:
        self._samplerate = samplerate
        self._max_seconds = max_seconds
        self._on_max_reached = on_max_reached
        self._input_device = input_device

        self._lock = threading.Lock()
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._timer: threading.Timer | None = None
        self._recording = False
        # Rate we actually captured at (may differ from self._samplerate if
        # the driver refused the target rate — see _open_stream). Only valid
        # while _recording is True.
        self._capture_samplerate: int | None = None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def start(self) -> None:
        """Open the input stream and begin buffering. Idempotent-ish: calling
        while already recording is a caller bug — raise so it doesn't hide."""
        with self._lock:
            if self._recording:
                raise RuntimeError("AudioRecorder.start() called while already recording")
            self._frames = []
            device = _resolve_device(self._input_device)
            capture_rate, stream = self._open_stream(device)
            try:
                stream.start()
            except Exception:
                # We built the stream but couldn't start it — clean up.
                try:
                    stream.close()
                except Exception:
                    pass
                raise
            self._stream = stream
            self._capture_samplerate = capture_rate
            self._recording = True

            # Arm the auto-stop timer. If it fires, we stop the stream from
            # a background thread and invoke the user callback so the app
            # can transition the state machine.
            self._timer = threading.Timer(self._max_seconds, self._auto_stop)
            self._timer.daemon = True
            self._timer.start()

        logger.debug("recorder: started (capture=%d Hz, target=%d Hz, max=%ds)",
                     capture_rate, self._samplerate, self._max_seconds)

    def _open_stream(self, device) -> tuple[int, "sd.InputStream"]:
        """Open the input stream, falling back to the device's native rate
        if the driver refuses the target rate. Returns (capture_rate, stream);
        the stream is not started yet."""
        def _build(rate: int) -> "sd.InputStream":
            kw: dict = dict(
                samplerate=rate,
                channels=1,
                dtype="float32",
                callback=self._on_audio,
                blocksize=int(rate * 0.1),   # 100 ms chunks
            )
            if device is not None:
                kw["device"] = device
            return sd.InputStream(**kw)

        try:
            return self._samplerate, _build(self._samplerate)
        except sd.PortAudioError as first_err:
            # The driver refused our target rate. Try again at the device's
            # native rate; stop() will resample down to self._samplerate.
            try:
                info = sd.query_devices(device, kind="input")
                native = int(round(float(info["default_samplerate"])))
            except Exception:
                raise first_err
            if native == self._samplerate:
                raise first_err
            logger.warning(
                "recorder: samplerate %d Hz refused (%s); "
                "falling back to device native %d Hz and resampling on stop",
                self._samplerate, first_err, native,
            )
            try:
                return native, _build(native)
            except sd.PortAudioError:
                # Fallback also failed — the original error is more
                # informative for the user.
                raise first_err

    def stop(self) -> np.ndarray:
        """Close the stream and return the captured audio as mono float32
        at ``self._samplerate`` (resampled if we had to capture at a
        different rate).

        Safe to call when not recording — returns an empty array."""
        with self._lock:
            if not self._recording:
                return np.zeros(0, dtype=np.float32)
            stream = self._stream
            timer = self._timer
            frames = self._frames
            capture_rate = self._capture_samplerate

            self._recording = False
            self._stream = None
            self._timer = None
            self._frames = []
            self._capture_samplerate = None

        if timer is not None:
            timer.cancel()

        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

        if not frames:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(frames).astype(np.float32, copy=False)
        if capture_rate is not None and capture_rate != self._samplerate:
            audio = _resample_linear(audio, capture_rate, self._samplerate)
        logger.debug("recorder: stopped, %d samples (%.2fs)",
                     len(audio), len(audio) / self._samplerate)
        return audio

    # -- internals ---------------------------------------------------------

    def _on_audio(self, indata, frames, time_info, status) -> None:
        """sounddevice callback. Runs on the audio thread; must be fast."""
        if status:
            logger.warning("recorder: sounddevice status: %s", status)
        # ``indata`` is (frames, channels). Take channel 0 for mono.
        if indata.ndim == 2 and indata.shape[1] >= 1:
            chunk = indata[:, 0]
        else:
            chunk = indata.reshape(-1)
        # ``indata`` may be a view onto a reused buffer — copy so the frames
        # we keep aren't overwritten by the next callback.
        self._frames.append(np.array(chunk, dtype=np.float32, copy=True))

    def _auto_stop(self) -> None:
        """Fires from the max-seconds Timer thread.

        We DO NOT call ``self.stop()`` here — the design contract is that
        the callback owner (the App) is responsible for the transition and
        will invoke ``stop()`` to drain the buffer. If we stopped first we
        would discard the audio the user just spoke."""
        with self._lock:
            if not self._recording:
                return
        logger.info("recorder: max_seconds reached, notifying app")
        cb = self._on_max_reached
        if cb is not None:
            try:
                cb()
            except Exception:  # pragma: no cover
                logger.exception("recorder: on_max_reached raised")
