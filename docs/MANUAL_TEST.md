# Manual test checklist

Automated tests cover the state machine, recorder, inserter, config, and
hotkey debounce. The following interactive scenarios still need a human.

Run `start.bat`, then walk through each item.

## Startup

- [ ] Tray icon appears within ~1 s (yellow).
- [ ] Icon turns gray after the model finishes loading (may take 10-60 s
  the first time — Whisper is downloading).
- [ ] No console window is visible.
- [ ] `%USERPROFILE%\.diktattool\diktattool.log` exists and contains
  a "diktattool starting" line.

## Basic dictation

- [ ] Open Notepad, press F12: tray turns red.
- [ ] Speak a short German sentence, press F12: tray turns yellow, then
  gray. Text appears in Notepad.
- [ ] Repeat in Chrome address bar → text appears.
- [ ] Repeat in VS Code → text appears.
- [ ] Repeat in a terminal (PowerShell / Windows Terminal) → text appears.

## Edge cases

- [ ] Press F12 while tray is yellow (transcribing): press is ignored, no
  state change; log shows "F12 ignored".
- [ ] Hold F12 down briefly (>200 ms): only one start-recording happens
  (debounce works).
- [ ] Start recording, wait 120 s without speaking again: recorder
  auto-stops; either transcription runs (may produce empty text and
  cleanly return to gray) or nothing is inserted. No crash.
- [ ] Immediately after inserting, press F12 again: works.
- [ ] Speak nothing between two F12 presses: no clipboard corruption
  (empty transcript short-circuits the insert).

## Failure paths

- [ ] Disable the default microphone in Windows sound settings, press F12:
  tray flashes red, tooltip mentions "Kein Mikrofon". State returns to
  gray. Re-enable the mic, press F12: works.
- [ ] Kill the network AND clear the huggingface cache, launch: model
  load fails, tray blinks red permanently. Log has a stack trace.

## Shutdown

- [ ] Right-click tray → **Beenden**: tray disappears; log has "exited
  cleanly". Task Manager shows no lingering `pythonw.exe`.
- [ ] Right-click tray → **Log öffnen…**: default editor opens the log.

## Latency (nice-to-have)

- [ ] Dictate ~10 seconds. Time between second F12 press and text
  appearing should be ≤ 3 s on a GTX 970.
