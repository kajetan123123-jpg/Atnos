@echo off
setlocal
cd /d "%~dp0\.."
".venv\Scripts\python.exe" "scripts\atnos_api_watchdog.py" --interval 60 --startup-wait 30
