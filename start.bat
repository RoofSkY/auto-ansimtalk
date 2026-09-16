@echo off
REM NOTE: keep this file ASCII-only (cmd.exe mis-parses multibyte chars in .bat)
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" server.py
) else (
    start "" pythonw server.py
)
exit
