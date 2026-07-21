@echo off
REM Build release artifacts into dist\ :
REM   1) auto-ansimtalk-v{version}.zip  (app bundle -> attach to GitHub Release)
REM   2) AnsimTalk-Setup.exe            (installer  -> attach to GitHub Release)
REM Prerequisite: pip install pyinstaller
REM NOTE: keep this file ASCII-only (cmd.exe mis-parses multibyte chars in .bat)
cd /d "%~dp0"

echo [1/2] Building release zip...
python tools\make_release_zip.py
if errorlevel 1 exit /b 1

echo.
echo [2/2] Building installer (AnsimTalk-Setup.exe)...
python -m PyInstaller --noconfirm --onefile --noconsole ^
    --icon "%~dp0static\app.ico" --name AnsimTalk-Setup ^
    --distpath dist --workpath build --specpath build ^
    installer\setup.py
if errorlevel 1 exit /b 1

echo.
echo ===== dist\ =====
dir /b dist
