"""Paste a transcribed string into the currently focused window.

The trick used by dictation tools everywhere: put the text on the clipboard,
then synthesize Ctrl+V. Universal (works in every text field on Windows),
cheap, and doesn't need per-application integration.

Trade-off: we clobber the user's clipboard. The design (§10) explicitly
accepts this — restoring it would require polling formats + delays that
break more workflows than they fix.
"""

from __future__ import annotations

import logging

import keyboard
import pyperclip


logger = logging.getLogger(__name__)


def insert_text(text: str) -> None:
    """Copy ``text`` (whitespace-trimmed) to the clipboard and send Ctrl+V.

    Empty / whitespace-only input is a silent no-op — Whisper occasionally
    returns those for pure noise and we don't want to overwrite the
    clipboard for nothing.
    """
    trimmed = text.strip()
    if not trimmed:
        logger.debug("inserter: skipping empty transcript")
        return

    logger.debug("inserter: copying %d chars", len(trimmed))
    # If pyperclip raises, don't send Ctrl+V — an old clipboard value would
    # get pasted, which is worse than nothing.
    pyperclip.copy(trimmed)
    keyboard.send("ctrl+v")
