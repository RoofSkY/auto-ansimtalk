@echo off
REM auto-ansimtalk launcher (background)
cd /d "%~dp0"
start "" pythonw server.py
start "" http://localhost:5000
exit
