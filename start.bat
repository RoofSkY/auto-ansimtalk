@echo off
REM auto-ansimtalk launcher (background)
REM NOTE: keep this file ASCII-only (cmd.exe mis-parses multibyte chars in .bat)
cd /d "%~dp0"
REM Prefer the project venv when present (dev machines).
REM Installed copies have no .venv, so they fall back to the PATH python
REM that the installer set up with the required packages.
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" server.py
) else (
    start "" pythonw server.py
)
exit
