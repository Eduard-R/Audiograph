"""System tray icon.

Icons are drawn programmatically with Pillow so the repo doesn't need to
carry PNG assets (open point §9 in the design, resolved this way). Each
status maps to a colored circle; the error state additionally uses
pystray's built-in ability to swap the icon image on the fly to blink.

pystray's event loop runs on the main thread and blocks — the orchestrator
calls :meth:`run` last, after starting the preload/worker threads.
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Callable

from PIL import Image, ImageDraw
import pystray


logger = logging.getLogger(__name__)


class Status(Enum):
    LOADING = "loading"      # yellow
    IDLE = "idle"            # gray
    RECORDING = "recording"  # red
    BUSY = "busy"            # yellow
    ERROR = "error"          # red_blink


_COLORS: dict[Status, tuple[int, int, int]] = {
    Status.LOADING: (240, 190, 40),
    Status.IDLE: (140, 140, 140),
    Status.RECORDING: (220, 40, 40),
    Status.BUSY: (240, 190, 40),
    Status.ERROR: (220, 40, 40),
}

_TITLES: dict[Status, str] = {
    Status.LOADING: "Diktattool — lade Modell…",
    Status.IDLE: "Diktattool — bereit (F12 drücken)",
    Status.RECORDING: "Diktattool — nimmt auf",
    Status.BUSY: "Diktattool — arbeitet",
    Status.ERROR: "Diktattool — Fehler",
}

# Windows Shell_NotifyIcon caps szTip at 128 wchar_t (incl. NUL). pystray
# raises ValueError if we exceed that. We truncate at 120 to leave headroom
# and append an ellipsis so long stack-trace-style errors still fit.
_MAX_TITLE = 120


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _make_icon(color: tuple[int, int, int], *, filled: bool = True) -> Image.Image:
    """Return a 64x64 RGBA circle. Empty circle for the blink 'off' frame."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if filled:
        d.ellipse((8, 8, 56, 56), fill=color + (255,))
    else:
        d.ellipse((8, 8, 56, 56), outline=color + (255,), width=4)
    return img


class TrayIcon:
    def __init__(
        self,
        on_quit: Callable[[], None],
        on_open_log: Callable[[], None] | None = None,
        on_restart: Callable[[], None] | None = None,
        get_tooltip: Callable[[], str] | None = None,
    ) -> None:
        self._on_quit = on_quit
        self._on_open_log = on_open_log or (lambda: None)
        self._on_restart = on_restart
        self._get_tooltip = get_tooltip

        self._status = Status.LOADING
        self._blink_stop: threading.Event | None = None
        self._blink_thread: threading.Thread | None = None

        items: list[pystray.MenuItem] = [
            pystray.MenuItem("Status", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Log öffnen…", self._menu_open_log),
        ]
        if on_restart is not None:
            items.append(pystray.MenuItem("Neustart", self._menu_restart))
        items.append(pystray.MenuItem("Beenden", self._menu_quit))

        self._icon = pystray.Icon(
            name="diktattool",
            title=_TITLES[Status.LOADING],
            icon=_make_icon(_COLORS[Status.LOADING]),
            menu=pystray.Menu(*items),
        )

    def run(self) -> None:
        """Block on the tray event loop. Returns when the icon is stopped."""
        logger.info("tray: entering event loop")
        self._icon.run()
        logger.info("tray: event loop exited")

    def set_status(self, status: Status, tooltip_suffix: str | None = None) -> None:
        """Thread-safe: called from the worker / preload / audio threads."""
        if status == self._status and status is not Status.ERROR:
            # ERROR is allowed to re-arm (new error resets the blink timer).
            return
        self._status = status

        self._stop_blink()

        title = _TITLES[status]
        if tooltip_suffix:
            title = f"{title} — {tooltip_suffix}"
        title = _truncate(title, _MAX_TITLE)

        if status is Status.ERROR:
            self._icon.title = title
            self._start_blink()
        else:
            self._icon.icon = _make_icon(_COLORS[status])
            self._icon.title = title

    # -- menu handlers -----------------------------------------------------

    def _menu_quit(self, icon, item) -> None:  # pragma: no cover - pystray glue
        logger.info("tray: quit requested")
        try:
            self._on_quit()
        finally:
            icon.stop()

    def _menu_restart(self, icon, item) -> None:  # pragma: no cover - pystray glue
        logger.info("tray: restart requested")
        try:
            if self._on_restart is not None:
                self._on_restart()
        finally:
            icon.stop()

    def _menu_open_log(self, icon, item) -> None:  # pragma: no cover
        self._on_open_log()

    # -- blink -------------------------------------------------------------

    def _start_blink(self) -> None:
        stop = threading.Event()
        color = _COLORS[Status.ERROR]

        def blink():
            filled = True
            # ~5 seconds of blinking, then hold red until the next event.
            elapsed = 0.0
            while not stop.is_set() and elapsed < 5.0:
                self._icon.icon = _make_icon(color, filled=filled)
                filled = not filled
                stop.wait(0.5)
                elapsed += 0.5
            # Land on a solid red so the tooltip is still discoverable.
            if not stop.is_set():
                self._icon.icon = _make_icon(_COLORS[Status.IDLE])

        self._blink_stop = stop
        self._blink_thread = threading.Thread(target=blink, name="tray-blink", daemon=True)
        self._blink_thread.start()

    def _stop_blink(self) -> None:
        if self._blink_stop is not None:
            self._blink_stop.set()
            self._blink_stop = None
            self._blink_thread = None
