@echo off
REM Rebuild static/tailwind.css from templates/ (run after changing Tailwind classes)
REM CLI: dev/bin/tailwindcss.exe (not in git - see README)
REM NOTE: keep this file ASCII-only (cmd.exe mis-parses multibyte chars in .bat)
cd /d "%~dp0.."
dev\bin\tailwindcss.exe -c dev\tailwind.config.js -i dev\tailwind.input.css -o static\tailwind.css --minify
