@echo off
REM Rebuild static/tailwind.css from templates/ (run after changing Tailwind classes)
REM CLI: _dev/tailwindcss.exe (see README)
REM NOTE: keep this file ASCII-only (cmd.exe mis-parses multibyte chars in .bat)
cd /d "%~dp0"
_dev\tailwindcss.exe -c tailwind.config.js -i tailwind.input.css -o static\tailwind.css --minify
