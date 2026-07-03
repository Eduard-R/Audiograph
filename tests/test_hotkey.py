"""Debounce test for HotkeyListener. The `keyboard` library itself isn't
exercised here — that's a manual test per design §7."""

from __future__ import annotations

from diktattool.hotkey import HotkeyListener


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt: float):
        self.t += dt


def test_debounce_swallows_rapid_reentry(monkeypatch):
    calls = []
    listener = HotkeyListener("f12", lambda: calls.append(1))
    clock = FakeClock()
    listener._monotonic = clock

    listener._fire()          # accepted
    clock.advance(0.05)
    listener._fire()          # too soon, debounced
    clock.advance(0.05)
    listener._fire()          # still too soon
    clock.advance(0.25)
    listener._fire()          # accepted again

    assert calls == [1, 1]
