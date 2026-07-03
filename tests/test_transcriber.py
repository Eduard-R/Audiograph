"""Tests for the Transcriber wrapper.

Two flavours:
- Unit tests that mock the faster-whisper model (fast, always run).
- One integration test that loads the real ``small`` model and transcribes a
  fixture WAV. Marked ``@pytest.mark.integration`` and skipped by default —
  run with ``pytest -m integration`` after downloading the model.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from diktattool import transcriber as transcriber_mod
from diktattool.transcriber import Transcriber


class FakeWhisperModel:
    def __init__(self, model_size, device, compute_type):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type

    def transcribe(self, audio, language, beam_size, vad_filter, condition_on_previous_text):
        segs = [
            SimpleNamespace(text=" Hallo"),
            SimpleNamespace(text=" Welt."),
        ]
        info = SimpleNamespace(language=language, language_probability=0.99)
        return segs, info


def test_transcribe_before_load_raises():
    t = Transcriber()
    with pytest.raises(RuntimeError):
        t.transcribe(np.zeros(1000, dtype=np.float32))


def test_load_then_transcribe_joins_segments(monkeypatch):
    def fake_import():
        return FakeWhisperModel

    # `Transcriber.load` does `from faster_whisper import WhisperModel`.
    # Inject a shim module so that deferred import resolves to the fake.
    import sys, types
    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)

    t = Transcriber(model_size="small", device="cpu", compute_type="int8", language="de")
    t.load()
    assert t.is_loaded

    audio = np.zeros(16000, dtype=np.float32)
    text = t.transcribe(audio)
    assert text == " Hallo Welt."


def test_load_is_idempotent(monkeypatch):
    import sys, types
    fake_mod = types.ModuleType("faster_whisper")
    calls = []

    class CountingModel(FakeWhisperModel):
        def __init__(self, *a, **kw):
            calls.append(1)
            super().__init__(*a, **kw)

    fake_mod.WhisperModel = CountingModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)

    t = Transcriber()
    t.load()
    t.load()
    assert len(calls) == 1


def test_empty_audio_returns_empty_string(monkeypatch):
    import sys, types
    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)

    t = Transcriber()
    t.load()
    assert t.transcribe(np.zeros(0, dtype=np.float32)) == ""


def test_load_falls_back_to_int8_when_first_choice_unsupported(monkeypatch):
    """Real failure mode on GTX 970 (Maxwell): int8_float16 raises ValueError
    from CTranslate2. We should retry with int8 and succeed."""
    import sys, types
    attempts: list[str] = []

    def factory(model_size, device, compute_type):
        attempts.append(compute_type)
        if compute_type == "int8_float16":
            raise ValueError("Requested int8_float16 compute type, but the "
                             "target device or backend do not support it.")
        return FakeWhisperModel(model_size, device, compute_type)

    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = factory
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)

    t = Transcriber(compute_type="int8_float16")
    t.load()
    assert attempts == ["int8_float16", "int8"]
    assert t.is_loaded


def test_load_raises_if_no_compute_type_works(monkeypatch):
    import sys, types

    def broken(model_size, device, compute_type):
        raise ValueError(f"{compute_type} not supported")

    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = broken
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)

    t = Transcriber(compute_type="int8_float16")
    with pytest.raises(RuntimeError):
        t.load()
    assert not t.is_loaded


# -- integration -----------------------------------------------------------

FIXTURE = Path(__file__).parent / "fixtures" / "test_de_hallo_welt.wav"


@pytest.mark.integration
@pytest.mark.skipif(not FIXTURE.exists(),
                    reason="fixture WAV missing — see tests/fixtures/README")
def test_integration_real_model_transcribes_german():  # pragma: no cover
    import wave

    with wave.open(str(FIXTURE), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        frames = w.readframes(w.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    t = Transcriber(model_size="small", device="cuda", compute_type="int8_float16", language="de")
    t.load()
    text = t.transcribe(audio).strip().lower()
    assert "hallo" in text
    assert "welt" in text
