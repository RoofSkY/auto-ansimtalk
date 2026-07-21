@echo off
REM 릴리스 산출물 빌드 — dist\ 에 두 파일 생성:
REM   1) auto-ansimtalk-v{버전}.zip  (앱 본체 — GitHub Release 에 첨부)
REM   2) AnsimTalk-Setup.exe         (설치 프로그램 — GitHub Release 에 첨부)
REM 사전 준비: pip install pyinstaller
cd /d "%~dp0"

echo [1/2] 릴리스 zip 생성...
python tools\make_release_zip.py
if errorlevel 1 exit /b 1

echo.
echo [2/2] 설치 프로그램(AnsimTalk-Setup.exe) 빌드...
python -m PyInstaller --noconfirm --onefile --noconsole ^
    --icon "%~dp0static\app.ico" --name AnsimTalk-Setup ^
    --distpath dist --workpath build --specpath build ^
    installer\setup.py
if errorlevel 1 exit /b 1

echo.
echo ===== dist\ 산출물 =====
dir /b dist
