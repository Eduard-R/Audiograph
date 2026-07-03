"""Orchestrator: wires hotkey, recorder, transcriber, inserter, tray.

Threads (design §4.3):
  main         — pystray event loop
  hotkey       — keyboard-lib internal
  audio        — sounddevice callback
  worker       — single worker, transcribe + insert
  model-preload — one-shot, loads Whisper into VRAM at startup
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from .config import Config, load_config, log_path, user_config_dir
from .hotkey import HotkeyListener
from .inserter import insert_text
from .recorder import AudioRecorder
from .state import Event, State, StateMachine
from .transcriber import Transcriber
from .tray import Status, TrayIcon


logger = logging.getLogger("diktattool")


_STATE_TO_TRAY: dict[State, Status] = {
    State.LOADING: Status.LOADING,
    State.IDLE: Status.IDLE,
    State.RECORDING: Status.RECORDING,
    State.TRANSCRIBING: Status.BUSY,
    State.INSERTING: Status.BUSY,
}


# Sentinel posted on the worker queue to signal shutdown.
_SHUTDOWN = object()


class App:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.sm = StateMachine()
        self.sm.on_transition = self._on_transition

        self.transcriber = Transcriber(
            model_size=cfg.model_size,
            device=cfg.device,
            compute_type=cfg.compute_type,
            language=cfg.language,
        )
        self.recorder = AudioRecorder(
            samplerate=cfg.samplerate,
            max_seconds=cfg.max_recording_seconds,
            on_max_reached=self._on_max_reached,
            input_device=cfg.input_device,
        )
        self.hotkey = HotkeyListener(cfg.hotkey, self._on_toggle)
        self.tray = TrayIcon(
            on_quit=self.shutdown,
            on_open_log=self._open_log_file,
            on_restart=self.restart,
        )

        self._worker_queue: queue.Queue[object] = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._preload_thread: Optional[threading.Thread] = None
        self._shutting_down = threading.Event()
        # Serializes finalization of a recording: F12 and the max-reached
        # timer both want to stop the mic and hand off audio; without this
        # lock they can both make it past the state check and double-queue.
        self._finalize_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        self._start_worker()
        self._start_preload()
        self.hotkey.start()
        # `tray.run()` blocks on the pystray event loop until quit.
        self.tray.run()
        self._join()

    def shutdown(self) -> None:
        if self._shutting_down.is_set():
            return
        logger.info("app: shutdown requested")
        self._shutting_down.set()
        try:
            self.hotkey.stop()
        except Exception:  # pragma: no cover
            logger.exception("app: hotkey.stop failed")
        try:
            self.recorder.stop()
        except Exception:  # pragma: no cover
            logger.exception("app: recorder.stop failed")
        self._worker_queue.put(_SHUTDOWN)

    def restart(self) -> None:
        """Spawn a fresh diktattool process, then shut this one down.

        The new process is detached so it survives our exit. If the spawn
        fails we still shut down cleanly — the user can relaunch manually
        via start.bat rather than being stuck with a half-dead tray icon."""
        if self._shutting_down.is_set():
            return
        logger.info("app: restart requested")
        try:
            self._spawn_new_instance()
        except Exception:
            logger.exception("app: could not spawn new instance")
        self.shutdown()

    @staticmethod
    def _spawn_new_instance() -> None:
        exe = sys.executable
        # Prefer pythonw.exe on Windows so no console window flashes for
        # the new instance (start.bat also uses pythonw).
        if sys.platform == "win32" and exe.lower().endswith("python.exe"):
            candidate = Path(exe).with_name("pythonw.exe")
            if candidate.exists():
                exe = str(candidate)

        kwargs: dict[str, object] = {"close_fds": True}
        if sys.platform == "win32":
            # DETACHED_PROCESS  — no console attached to the child
            # NEW_PROCESS_GROUP — child ignores our Ctrl+C group
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:  # pragma: no cover — target platform is Windows
            kwargs["start_new_session"] = True

        subprocess.Popen([exe, "-m", "diktattool"], **kwargs)
        logger.info("app: spawned new diktattool instance (%s)", exe)

    def _join(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)

    # -- state transitions -------------------------------------------------

    def _on_transition(self, old: State, new: State) -> None:
        logger.info("app: state %s -> %s", old.name, new.name)
        tooltip = self.sm.last_error
        tray_status = _STATE_TO_TRAY.get(new, Status.IDLE)
        try:
            self.tray.set_status(tray_status, tooltip_suffix=tooltip)
        except Exception:  # pragma: no cover - pystray icon may not be up yet
            logger.exception("app: tray.set_status failed")

    # -- hotkey callback ---------------------------------------------------

    def _on_toggle(self) -> None:
        current = self.sm.state
        if current is State.IDLE:
            # Start recording synchronously; the audio callback runs on the
            # sounddevice thread and pushes frames into the recorder's buffer.
            try:
                self.recorder.start()
            except Exception as e:
                logger.exception("app: recorder.start failed")
                self.sm.handle(Event.ERROR, message=f"Kein Mikrofon: {e}")
                self._flash_error()
                return
            self.sm.handle(Event.TOGGLE)

        elif current is State.RECORDING:
            self._finalize_recording(Event.TOGGLE)

        else:
            logger.debug("app: F12 ignored in state %s", current.name)

    def _on_max_reached(self) -> None:
        """Called from the recorder's Timer thread when max_seconds elapses."""
        logger.info("app: max_seconds reached — auto-stop")
        self._finalize_recording(Event.MAX_REACHED)

    def _finalize_recording(self, event: Event) -> None:
        """Serialized stop-and-hand-off. Whichever caller (F12 or max-timer)
        gets here first drains the mic and queues the audio; the loser
        finds the state already changed and bails out."""
        with self._finalize_lock:
            if self.sm.state is not State.RECORDING:
                return
            try:
                audio = self.recorder.stop()
            except Exception as e:
                logger.exception("app: recorder.stop failed on finalize")
                self.sm.handle(Event.ERROR, message=str(e))
                self._flash_error()
                return
            self.sm.handle(event)  # -> TRANSCRIBING
            self._worker_queue.put(audio)

    def _flash_error(self) -> None:
        try:
            self.tray.set_status(Status.ERROR, tooltip_suffix=self.sm.last_error)
        except Exception:  # pragma: no cover
            logger.exception("app: could not flash error tray")

    # -- preload thread ----------------------------------------------------

    def _start_preload(self) -> None:
        def _load():
            try:
                self.transcriber.load()
            except Exception as e:
                logger.exception("app: model load failed")
                self.sm.handle(Event.MODEL_LOAD_FAILED, message=str(e))
                self._flash_error()
                return
            self.sm.handle(Event.MODEL_LOADED)

        t = threading.Thread(target=_load, name="model-preload", daemon=True)
        t.start()
        self._preload_thread = t

    # -- worker thread -----------------------------------------------------

    def _start_worker(self) -> None:
        t = threading.Thread(target=self._worker_loop, name="worker", daemon=True)
        t.start()
        self._worker_thread = t

    def _worker_loop(self) -> None:
        while True:
            item = self._worker_queue.get()
            if item is _SHUTDOWN:
                logger.info("worker: shutdown signal received")
                return
            audio = item  # type: np.ndarray
            try:
                self._process(audio)
            except Exception as e:  # catch-all: worker must not die
                logger.exception("worker: unhandled error")
                self.sm.handle(Event.ERROR, message=str(e))
                self._flash_error()

    def _process(self, audio: np.ndarray) -> None:
        if audio.size == 0:
            logger.warning("worker: empty audio, skipping")
            self.sm.handle(Event.TRANSCRIPTION_DONE)
            self.sm.handle(Event.INSERT_DONE)
            return

        # Transcribe.
        try:
            text = self.transcriber.transcribe(audio)
        except Exception as e:
            logger.exception("worker: transcription failed (audio=%.2fs)",
                             audio.size / max(self.cfg.samplerate, 1))
            self.sm.handle(Event.ERROR, message=f"Transkription: {e}")
            self._flash_error()
            return

        self.sm.handle(Event.TRANSCRIPTION_DONE)

        # Insert.
        try:
            insert_text(text)
        except Exception as e:
            logger.exception("worker: insert failed (text kept in log at DEBUG)")
            logger.debug("worker: failed text was %r", text)
            self.sm.handle(Event.ERROR, message=f"Einfügen: {e}")
            self._flash_error()
            return

        self.sm.handle(Event.INSERT_DONE)

    # -- log file ----------------------------------------------------------

    def _open_log_file(self) -> None:  # pragma: no cover
        p = log_path()
        if not p.exists():
            logger.info("app: log file %s not present yet", p)
            return
        try:
            os.startfile(str(p))  # Windows-native "open with default app"
        except OSError:
            subprocess.Popen(["notepad.exe", str(p)])


# -- entry point -----------------------------------------------------------


def _configure_logging(level: str) -> None:
    d = user_config_dir()
    d.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.handlers.RotatingFileHandler(
        log_path(), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(fmt)
    root.addHandler(handler)

    lvl = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(lvl)


def main() -> int:
    cfg = load_config()
    _configure_logging(cfg.log_level)
    logger.info(
        "diktattool starting (model=%s device=%s lang=%s hotkey=%s)",
        cfg.model_size, cfg.device, cfg.language, cfg.hotkey,
    )
    try:
        App(cfg).run()
    except KeyboardInterrupt:
        logger.info("diktattool interrupted")
    except Exception:
        logger.exception("diktattool: fatal error")
        return 1
    logger.info("diktattool exited cleanly")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
