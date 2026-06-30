## Anforderungen

Entwicklung einer lokalen Windows-Anwendung, die es ermöglicht, systemweit per Hotkey (F12) Sprache aufzunehmen, lokal zu transkribieren (Faster-Whisper) und den Text automatisch in das aktuell aktive Textfeld einzufügen, unabhängig von der verwendeten Anwendung (z.B. Claude Code, Antigravity, Hermes Agent, Browser, IDE's). Hintergrunddienst ohne sichtbare UI. Deutsche Sprache als Default. Audioaufnahme über Standardmikrofon.

## Tech Stack

- Python
- faster-whisper
- AutoHotkey (AHK)
- sounddevice
- pyperclip

## Hardware

- GTX-970