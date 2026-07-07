# Diktattool

Local Whisper dictation for Windows — press **F12**, speak, press **F12** again, the
transcribed text lands at your cursor. No cloud, no subscription, no visible window.

Details of the approved design: [`docs/superpowers/specs/2026-06-30-diktattool-design.md`](docs/superpowers/specs/2026-06-30-diktattool-design.md).

## Requirements

- Windows 10 / 11
- Python 3.11+ (from python.org, tick **Add to PATH** during install)
- CPU works out of the box. Optional: NVIDIA GPU with **compute capability 7.0+**
  (Volta / RTX 20xx or newer) for CUDA. Older cards (e.g. GTX 9xx / Maxwell)
  must stay on `device = "cpu"` — CTranslate2 4.x dropped their support.
- **Administrator rights** when launching — the `keyboard` library needs them
  to install the global F12 hook.

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

### Installing on another machine

1. Copy the project folder (or `git clone`). **Do not copy** `.venv\`,
   `__pycache__\`, or your personal `%USERPROFILE%\.diktattool\config.toml` —
   the venv contains absolute paths and won't work elsewhere.
2. Run the Install steps above on the target machine.
3. Adjust `%USERPROFILE%\.diktattool\config.toml` to the new hardware:
   - Modern NVIDIA GPU → `device = "cuda"`, `compute_type = "float16"`
     (or `"int8_float16"` for less VRAM).
   - No NVIDIA / older GPU → `device = "cpu"`, `compute_type = "int8"`.
4. Make sure Windows has a default recording device set, or list what
   PortAudio sees and pin it via `input_device` (see `config.example.toml`).
5. Some antivirus tools flag the `keyboard` library as a keylogger — add
   an exception for the project folder if a scan quarantines it.

## Run

Right-click `start.bat` → **Als Administrator ausführen** (or launch cmd
as admin and run it there). Without admin the tray icon appears but F12
does nothing.

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
