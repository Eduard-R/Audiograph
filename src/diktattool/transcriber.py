"""Wrapper around ``faster-whisper``.

Kept intentionally thin: :meth:`load` warms up the model once (called from
the preload thread at startup so the tray isn't stuck yellow on first F12),
:meth:`transcribe` runs on the worker thread. The underlying
``WhisperModel`` is safe to call from a single thread at a time — we
serialize via the app's single worker.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np


logger = logging.getLogger(__name__)


class Transcriber:
    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "de",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language

        self._model = None                        # type: Optional[object]
        self._load_lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Instantiate the model. Blocks until the weights are loaded onto
        the chosen device. Called once from the preload thread.

        Deferred import of ``faster_whisper`` keeps unit tests (and the
        rest of the code) importable even without CTranslate2.

        If the requested compute type isn't supported on this device, we
        fall back through progressively more conservative options rather
        than leaving the app dead."""
        with self._load_lock:
            if self._model is not None:
                return
            from faster_whisper import WhisperModel  # deferred import

            # Try the requested compute type first, then safe fallbacks.
            candidates = [self._compute_type]
            for fb in ("int8", "int8_float32", "float32"):
                if fb not in candidates:
                    candidates.append(fb)

            t0 = time.perf_counter()
            last_err: Exception | None = None
            for i, compute in enumerate(candidates):
                try:
                    logger.info(
                        "transcriber: loading model=%s device=%s compute=%s%s",
                        self._model_size, self._device, compute,
                        " (fallback)" if i > 0 else "",
                    )
                    self._model = WhisperModel(
                        self._model_size,
                        device=self._device,
                        compute_type=compute,
                    )
                    if i > 0:
                        logger.warning(
                            "transcriber: %r not supported here — using %r instead",
                            self._compute_type, compute,
                        )
                    dt = time.perf_counter() - t0
                    logger.info("transcriber: model loaded in %.1fs", dt)
                    return
                except ValueError as e:
                    # CTranslate2 raises ValueError for unsupported compute
                    # types. Try the next fallback.
                    last_err = e
                    logger.warning("transcriber: compute=%s rejected: %s", compute, e)
                    continue

            # All fallbacks exhausted — surface the last error.
            raise RuntimeError(
                f"faster-whisper could not load with any supported compute type: {last_err}"
            )

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a mono float32 16 kHz array and return the joined text."""
        if self._model is None:
            raise RuntimeError("Transcriber.transcribe() called before load()")
        if audio.size == 0:
            return ""

        t0 = time.perf_counter()
        # `condition_on_previous_text=False` prevents Whisper from carrying
        # hallucinated context between short back-to-back dictations.
        segments, info = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        text = "".join(seg.text for seg in segments)
        dt = time.perf_counter() - t0
        logger.info(
            "transcriber: %.2fs audio -> %d chars in %.2fs (rt=%.1fx)",
            audio.size / 16000, len(text), dt,
            (audio.size / 16000) / max(dt, 0.001),
        )
        return text
