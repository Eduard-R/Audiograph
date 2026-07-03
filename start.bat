@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
    echo Virtualenv not found. Run: python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install -r requirements.txt
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
start "" pythonw -m diktattool
