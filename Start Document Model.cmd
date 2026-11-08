@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m tools.start_document_model
) else (
    echo Install the project dependencies first.
)
pause
