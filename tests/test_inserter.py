"""Unit tests for ``insert_text``. Clipboard and keyboard are mocked."""

from __future__ import annotations

import pytest

from diktattool import inserter as inserter_mod
from diktattool.inserter import insert_text


@pytest.fixture
def mocked(monkeypatch):
    calls = {"copy": [], "send": []}

    def fake_copy(text):
        calls["copy"].append(text)

    def fake_send(keys):
        calls["send"].append(keys)

    monkeypatch.setattr(inserter_mod.pyperclip, "copy", fake_copy)
    monkeypatch.setattr(inserter_mod.keyboard, "send", fake_send)
    return calls


def test_happy_path_copies_and_sends_paste(mocked):
    insert_text("Hallo Welt")
    assert mocked["copy"] == ["Hallo Welt"]
    assert mocked["send"] == ["ctrl+v"]


def test_whitespace_is_trimmed(mocked):
    insert_text("   Hallo Welt.   \n")
    assert mocked["copy"] == ["Hallo Welt."]


def test_empty_input_is_noop(mocked):
    insert_text("")
    assert mocked["copy"] == []
    assert mocked["send"] == []


def test_whitespace_only_input_is_noop(mocked):
    insert_text("   \n\t  ")
    assert mocked["copy"] == []
    assert mocked["send"] == []


def test_clipboard_error_does_not_send_paste(monkeypatch):
    """Design §6: clipboard write-protected → log WARN, state → IDLE, no paste."""
    sent: list[str] = []

    def broken_copy(text):
        raise inserter_mod.pyperclip.PyperclipException("clipboard busy")

    monkeypatch.setattr(inserter_mod.pyperclip, "copy", broken_copy)
    monkeypatch.setattr(inserter_mod.keyboard, "send", lambda k: sent.append(k))

    with pytest.raises(inserter_mod.pyperclip.PyperclipException):
        insert_text("hi")
    assert sent == []


def test_keyboard_send_error_propagates(monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(inserter_mod.pyperclip, "copy", lambda t: copied.append(t))

    def broken_send(keys):
        raise OSError("keyboard hook lost")

    monkeypatch.setattr(inserter_mod.keyboard, "send", broken_send)
    with pytest.raises(OSError):
        insert_text("hi")
    # Clipboard already carries the text — the user can Ctrl+V by hand.
    assert copied == ["hi"]
