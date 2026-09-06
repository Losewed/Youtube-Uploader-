@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" dashboard.py
) else (
    start "" pythonw dashboard.py
)
