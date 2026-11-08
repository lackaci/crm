@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m app.test_main
) else (
    echo Install the project dependencies first, then run: python -m app.test_main
    pause
)
