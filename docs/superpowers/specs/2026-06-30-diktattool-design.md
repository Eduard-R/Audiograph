# PRD – Diktattool (Local Whisper Dictation für Windows)

**Datum:** 2026-06-30
**Status:** Approved Design – ready for implementation planning
**Autor:** Brainstorming Session

---

## 1. Zielsetzung

Entwicklung einer lokalen Windows-Anwendung, die das Diktieren von Text in beliebige Anwendungen ermöglicht – funktional vergleichbar mit Wispr Flow / SuperWhisper, aber **vollständig lokal** und **ohne Abo**. Der User drückt einen globalen Hotkey (F12), spricht, drückt F12 erneut, und der transkribierte Text erscheint an der aktuellen Cursorposition in der aktiven Anwendung.

### Erfolgskriterien

1. F12 als globaler Hotkey funktioniert in allen Standard-Anwendungen (Browser, IDE, Terminal, Office, Claude Code, Antigravity, Hermes Agent).
2. Latenz vom Stoppen der Aufnahme bis zum eingefügten Text: ≤ 3 Sekunden für 10-Sekunden-Diktate auf einer GTX 970.
3. Deutsche Transkriptionsqualität: gut genug für normale Diktate (typische Wortfehlerrate < 10% bei klarem Sprechen).
4. Hintergrundprozess ohne sichtbares Hauptfenster – Sichtbarkeit nur über ein Systray-Icon.
5. Keine Cloud-Calls, keine Telemetry, keine externen Abhängigkeiten zur Laufzeit.

---

## 2. Scope

### In Scope
- Toggle-Aufnahme via F12 (drücken = Start, erneut drücken = Stop)
- Lokale Transkription mit `faster-whisper`, Modell `small`, CUDA, `int8_float16`
- Fix konfigurierte Sprache Deutsch (`de`)
- Einfügen via Clipboard + simuliertes Strg+V
- Systray-Icon mit Statusfarbe als einziges UI-Element
- Manueller Start (kein Autostart), Distribution via Python-venv + `start.bat`
- Hard-Limit 2 Minuten pro Aufnahme
- Whitespace-Trim als einziges Post-Processing
- Logging in Rotating-File unter `%USERPROFILE%\.diktattool\`

### Out of Scope
- Autostart mit Windows
- Mehrsprachen-Support / Auto-Detect
- Voice Activity Detection / automatisches Trimmen von Stille
- Filler-Word-Removal, Auto-Capitalization
- Live-Streaming-Transkription (während gesprochen wird)
- UI-Dialoge oder Toast-Notifications
- Standalone-Executable (PyInstaller)
- Windows Service
- Push-to-Talk-Modus
- Custom Hotkeys jenseits des Config-Eintrags
- Backup/Wiederherstellung des Clipboard-Inhalts
- Cloud-Sync, Multi-Device, Telemetry

---

## 3. Tech Stack & Hardware

| Komponente | Wahl |
|---|---|
| Sprache | Python 3.11+ |
| Transcription Engine | `faster-whisper` (CTranslate2) |
| Audio Capture | `sounddevice` |
| Hotkey | `keyboard` (Python-Lib) |
| Clipboard | `pyperclip` |
| Tray Icon | `pystray` + `Pillow` |
| Tests | `pytest`, `pytest-mock` |
| Config | TOML (stdlib `tomllib`) |

**Hardware-Ziel:** NVIDIA GTX 970 (Maxwell, 4 GB VRAM, effektiv ~3,5 GB). Modell `small` mit `int8_float16`-Quantisierung passt mit Reserve.

**Nicht im Stack:** AutoHotkey wurde während Brainstorming verworfen – Python-`keyboard` reicht für F12 (nicht reserviert) und vereinfacht Architektur auf einen Prozess.

---

## 4. Architektur

### 4.1 Komponentenübersicht

```
app.py (Orchestrator)
├── State Machine + EventBus
├── hotkey.py    – globaler F12-Listener
├── recorder.py  – Mikrofon-Aufnahme via sounddevice
├── transcriber.py – Wrapper um faster-whisper
├── inserter.py  – Clipboard + Strg+V
└── tray.py      – Systray-Icon-Anzeige
```

### 4.2 State Machine

Sechs Zustände, deterministische Übergänge:

```
LOADING ──model_loaded──▶ IDLE ──F12──▶ RECORDING ──F12 or max_reached──▶ TRANSCRIBING
                            ▲                                                  │
                            │                                                  ▼
                            └──insert_done── INSERTING ◀──transcription_done──┘

beliebiger State außer LOADING ──error──▶ ERROR ──(sofort)──▶ IDLE
                                            │
                                            └── Tray-Icon rot blinkend für 5 s,
                                                danach grau (oder bis nächstes F12)
```

- **`LOADING`**: Initial-State beim App-Start. Modell wird in Worker-Thread geladen. F12 wird ignoriert. Tray: gelb.
- **`IDLE`**: bereit für Aufnahme. Tray: grau.
- **`RECORDING`**: Mikrofon offen, Buffer wächst. Tray: rot.
- **`TRANSCRIBING`**: Whisper läuft. Tray: gelb.
- **`INSERTING`**: Clipboard + Strg+V. Tray: gelb.
- **`ERROR`**: transienter Marker-State, wechselt sofort zu `IDLE`. Tray zeigt 5 s rot blinkend, dann grau. Last-Error-Text bleibt im Tooltip bis zum nächsten erfolgreichen Diktat.

**Wichtige Invarianten:**
- F12 während `LOADING`, `RECORDING`-Übergang läuft, `TRANSCRIBING`, `INSERTING` wird ignoriert.
- Im `RECORDING`-State stoppt F12 die Aufnahme (das ist der Toggle).
- Im `IDLE` (auch wenn Tray gerade rot blinkt nach einem Fehler) wird F12 normal verarbeitet.
- Es gibt nie zwei gleichzeitige Aufnahmen oder Transkriptionen.
- Worker-Thread bearbeitet sequenziell – Reihenfolge der eingefügten Texte ist garantiert.
- Beim Modell-Load-Fehler bleibt die App in `LOADING` mit Tray rot blinkend permanent (siehe §6).

### 4.3 Thread-Modell

| Thread | Aufgabe |
|---|---|
| Main | `pystray` Event-Loop (blockierend) |
| Hotkey | von `keyboard`-Lib intern verwaltet |
| Audio | `sounddevice`-Callback (pusht Frames in Queue) |
| Worker | Transkription + Insert, einzelner Worker |
| Model-Preload | einmaliger Start-Thread, lädt Modell in VRAM |

### 4.4 Modul-Interfaces

**`hotkey.py`**
```python
class HotkeyListener:
    def __init__(self, key: str, on_toggle: Callable[[], None]): ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```
Debouncing: 200 ms Cooldown gegen Key-Repeats.

**`recorder.py`**
```python
class AudioRecorder:
    def __init__(self, samplerate: int = 16000, max_seconds: int = 120,
                 on_max_reached: Callable[[], None] = None): ...
    def start(self) -> None: ...
    def stop(self) -> np.ndarray: ...   # mono float32, 16 kHz
    @property
    def is_recording(self) -> bool: ...
```
Frame-basiertes Append (z.B. 100 ms Chunks). Auto-Stop-Timer triggert `on_max_reached`.

**`transcriber.py`**
```python
class Transcriber:
    def __init__(self, model_size: str = "small", device: str = "cuda",
                 compute_type: str = "int8_float16", language: str = "de"): ...
    def load(self) -> None: ...
    def transcribe(self, audio: np.ndarray) -> str: ...
    @property
    def is_loaded(self) -> bool: ...
```
Lädt Modell einmal, danach Threadsafe-`transcribe()`-Aufrufe aus Worker.

**`inserter.py`**
```python
def insert_text(text: str) -> None: ...
```
Stateless. Whitespace-Trim, leerer String → no-op. Setzt `pyperclip.copy()`, sendet `keyboard.send("ctrl+v")`.

**`tray.py`**
```python
class TrayIcon:
    def __init__(self, on_quit: Callable[[], None]): ...
    def run(self) -> None: ...
    def set_status(self, status: Status) -> None: ...

class Status(Enum):
    LOADING = "yellow"
    IDLE = "gray"
    RECORDING = "red"
    BUSY = "yellow"
    ERROR = "red_blink"
```
Tray-Menü: read-only Status + Quit-Eintrag + "Log öffnen…".

**`app.py`** – verdrahtet alle Module, hält State Machine, spawnt Worker- und Preload-Threads.

**`config.py`**
```python
@dataclass
class Config:
    hotkey: str = "f12"
    model_size: str = "small"
    language: str = "de"
    max_recording_seconds: int = 120
    samplerate: int = 16000
    compute_type: str = "int8_float16"
    log_level: str = "INFO"
```
Geladen aus `%USERPROFILE%\.diktattool\config.toml` falls vorhanden, sonst Defaults.

---

## 5. Datenfluss eines Diktats

```
t=0.0s   F12 → HotkeyListener → EventBus("toggle")
         → StateMachine: IDLE → RECORDING
         → TrayIcon rot
         → AudioRecorder.start()

t=0.0..4.2s   User spricht; sounddevice akkumuliert Frames

t=4.2s   F12 → EventBus("toggle")
         → audio = AudioRecorder.stop()
         → StateMachine: RECORDING → TRANSCRIBING
         → TrayIcon gelb
         → Worker.submit(transcribe_and_insert, audio)

[Worker]
t=4.3s   Transcriber.transcribe(audio)
t=5.1s   → " Hallo Welt, das ist ein Test."
         → StateMachine: TRANSCRIBING → INSERTING
         → insert_text(...)
           ├── trim
           ├── pyperclip.copy()
           └── keyboard.send("ctrl+v")
t=5.2s   Text im aktiven Textfeld
         → StateMachine: INSERTING → IDLE
         → TrayIcon grau
```

Latenz-Budget für 10-Sekunden-Diktat: Recording-Stop bis Text-eingefügt soll ≤ 3 s sein.

---

## 6. Error Handling

| Fehlerquelle | Reaktion |
|---|---|
| Mikrofon nicht verfügbar | Log ERROR, Tray rot blinkend, State → IDLE, Tooltip "Kein Mikrofon" |
| CUDA / Modell-Load fehlgeschlagen | Log ERROR mit Stacktrace, Tray rot blinkend permanent, App nicht funktional aber lebendig |
| Whisper-Exception | Log ERROR mit Audio-Länge, Audio verwerfen, State → ERROR → IDLE, kein Retry |
| Clipboard schreibgeschützt | Log WARN, State → IDLE; Transkript bleibt in Log |
| F12-Doppeltrigger < 200 ms | Stillschweigend ignoriert |
| Worker-Thread-Crash | Catch-All im Worker-Loop, Log, State zurück zu IDLE |

**User-sichtbare Signale:**
- Tray-Icon-Farbe (rot blinkend = letzter Versuch fehlgeschlagen)
- Tray-Tooltip on hover (Kurztext des letzten Fehlers)
- Log-Datei (`Log öffnen…`-Menüpunkt)

**Bewusst nicht implementiert:** Toast-Notifications, UI-Dialoge, Auto-Retry, Telemetry, Crash-Reporting.

### Logging

- stdlib `logging`, Rotating-File 5 MB × 3.
- Pfad: `%USERPROFILE%\.diktattool\diktattool.log`.
- Format: `2026-06-30 14:23:01 [INFO] app: state RECORDING -> TRANSCRIBING (audio=4.2s)`.
- Geloggt werden: State-Transitions, Audio-Längen, Transkriptions-Dauer, Modell-Load-Zeit, Fehler.
- **Nicht** geloggt auf INFO-Level: der transkribierte Text (Privacy). Auf DEBUG ja, für Diagnose.

---

## 7. Testing

| Komponente | Test-Typ |
|---|---|
| `app.StateMachine` | Unit (kein I/O) – wichtigste Tests |
| `recorder.AudioRecorder` | Unit (mock sounddevice) |
| `inserter.insert_text` | Unit (mock pyperclip + keyboard) |
| `transcriber.Transcriber` | Integration (`@pytest.mark.integration`) mit echtem Modell und Test-WAV |
| `hotkey.HotkeyListener` | Manuell – keine Auto-Tests |
| `tray.TrayIcon` | Manuell – GUI |
| End-to-End | Manueller Testplan in `docs/MANUAL_TEST.md` |

**Manueller E2E-Checklisten-Beispiele:** Tray erscheint nach Start, F12 in Notepad/Browser/VSCode funktioniert, 2-Min-Auto-Stop, F12-Ignore während TRANSCRIBING, Mikrofon-Wegnahme-Recovery, sauberes Quit.

**Bewusst nicht implementiert:** CI/CD, Coverage-Gates, Performance-Benchmarks, UI-Automation.

---

## 8. Projektstruktur

```
Diktattool/
├── Anforderungen.md
├── README.md
├── docs/
│   ├── superpowers/specs/
│   │   └── 2026-06-30-diktattool-design.md
│   └── MANUAL_TEST.md
├── src/diktattool/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── config.py
│   ├── hotkey.py
│   ├── recorder.py
│   ├── transcriber.py
│   ├── inserter.py
│   └── tray.py
├── tests/
│   ├── test_state_machine.py
│   ├── test_recorder.py
│   ├── test_inserter.py
│   ├── test_transcriber.py
│   └── fixtures/test_de_hallo_welt.wav
├── assets/
│   ├── tray_idle.png
│   ├── tray_recording.png
│   ├── tray_busy.png
│   └── tray_error.png
├── config.example.toml
├── requirements.txt
├── start.bat
└── .gitignore
```

### Dependencies (`requirements.txt`)

```
faster-whisper>=1.0.0
sounddevice>=0.4.6
numpy>=1.24
pyperclip>=1.8.2
keyboard>=0.13.5
pystray>=0.19.5
Pillow>=10.0
```

CUDA-Runtime kommt über CTranslate2-Wheels bei `faster-whisper` – kein separater CUDA-Toolkit-Install.

### `start.bat`

```bat
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
start "" pythonw -m diktattool
```

`pythonw` unterdrückt Console-Fenster; `start ""` löst Batch vom Prozess.

### Setup-Befehle

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy config.example.toml %USERPROFILE%\.diktattool\config.toml
start.bat
```

### Modell-Storage

`faster-whisper` cached Modelle in `%USERPROFILE%\.cache\huggingface\hub\`. `small` ist ein einmaliger Download von ~488 MB. Erster App-Start dauert länger (Tray bleibt gelb), das ist normal.

---

## 9. Offene Punkte für Implementation Plan

Folgende Detailentscheidungen werden im nachgelagerten Implementation Plan getroffen, nicht im Design:

- Konkretes Frame-Format / Chunk-Größe in `recorder.py`
- EventBus: einfache Liste von Callbacks vs. `queue.Queue` – wahrscheinlich Letzteres
- Genaues Verhalten von `tray.set_status` – Reload-Icon-Datei vs. Live-Pixel-Update
- Test-Fixture-WAV: woher (selbst aufgenommen vs. generiert)
- Icon-Assets: Quelle / Lizenz / Pixelgröße

---

## 10. Decision Log

| Entscheidung | Begründung |
|---|---|
| Toggle statt Push-to-Talk | Hände frei bei längeren Diktaten |
| Nur Systray-Icon als Feedback | Minimaler UI-Footprint, ausreichend für Single-User |
| Modell `small` | Bester Trade-off auf GTX 970: gute deutsche Qualität, ~2 s Latenz |
| Clipboard + Strg+V (ohne Restore) | Einfach, universell, akzeptabler Trade-off |
| Python-only ohne AHK | Eine Sprache, ein Prozess, kein IPC |
| Fix Deutsch | Beste Qualität für deutschen Workflow, kein Detection-Overhead |
| Manuelles Start | Kein Autostart-Footprint |
| venv + start.bat | Keine PyInstaller-Komplexität, transparenter Build |
| 2-Min-Hard-Limit | Schutz vor "vergessen zu stoppen" |
| Nur Whitespace-Trim | Whisper-Output ist gut genug, weniger ist mehr |
| Single Worker Thread | Reihenfolge garantiert, kein Race-Risk |
| Kein Auto-Retry | User merkt's und drückt einfach erneut |
| Transkript nicht auf INFO loggen | Privacy-by-default |
