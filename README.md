# Diktattool

Local Whisper dictation for Windows — press **F12**, speak, press **F12** again, the
transcribed text lands at your cursor. No cloud, no subscription, no visible window.

Details of the approved design: [`docs/superpowers/specs/2026-06-30-diktattool-design.md`](docs/superpowers/specs/2026-06-30-diktattool-design.md).

## Requirements

- Windows 10 / 11
- Python 3.11+
- NVIDIA GPU with CUDA (default config targets a GTX 970; adjust `compute_type` in
  the config for other cards, or set `device = "cpu"`)

## Install

In **cmd.exe**:

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
mkdir "%USERPROFILE%\.diktattool"
copy config.example.toml "%USERPROFILE%\.diktattool\config.toml"
```

In **PowerShell**:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.diktattool" | Out-Null
Copy-Item config.example.toml "$env:USERPROFILE\.diktattool\config.toml"
```

If PowerShell blocks `Activate.ps1` (`... da die Ausführung von Skripts auf
diesem System deaktiviert ist`):

```powershell
# one-off, this shell only:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass ; .venv\Scripts\Activate.ps1

# or permanent for your user (recommended):
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

The config copy is optional — the app runs on built-in defaults if the
file is missing. `%USERPROFILE%\.diktattool\` is created automatically on
first launch (for the log file).

The first launch downloads the Whisper model (~488 MB for `small`) into
`%USERPROFILE%\.cache\huggingface\hub\`. Subsequent starts skip that.

## Run

```bat
start.bat
```

Runs headless — look for the tray icon:

| Color | Meaning |
|---|---|
| yellow | model loading / transcribing / inserting |
| gray   | idle, ready for F12 |
| red    | recording |
| red blinking | last dictation errored (hover for details) |

Right-click the tray icon for **Log öffnen…** and **Beenden**.

## Config

Edit `%USERPROFILE%\.diktattool\config.toml`. All keys are optional; missing
ones fall back to the built-in defaults. See `config.example.toml` for the
full list.

## Logs

`%USERPROFILE%\.diktattool\diktattool.log` — 5 MB × 3 rotating files.
Transcribed text is only logged at DEBUG level.

## Development

```bat
pip install -r requirements-dev.txt
pytest                       # unit tests
pytest -m integration        # real model + fixture WAV (see tests/fixtures/README)
```

Manual E2E checklist in [`docs/MANUAL_TEST.md`](docs/MANUAL_TEST.md).
