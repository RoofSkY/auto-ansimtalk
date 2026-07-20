@echo off
REM templates/ 의 클래스를 스캔해 static/tailwind.css 재생성
REM (화면 클래스를 수정한 뒤 실행 — CLI 는 _dev/tailwindcss.exe, README 참고)
cd /d "%~dp0"
_dev\tailwindcss.exe -c tailwind.config.js -i tailwind.input.css -o static\tailwind.css --minify
