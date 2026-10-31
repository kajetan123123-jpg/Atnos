@echo off
setlocal
cd /d "%~dp0\.."
".venv\Scripts\python.exe" "scripts\manage_atnos_watchdog_task.py" uninstall
