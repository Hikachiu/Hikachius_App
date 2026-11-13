@echo off
rem Boatrace app server for home Wi-Fi (phones on the same Wi-Fi can open it).
rem Double-click to start. Close this window to stop.
cd /d "%~dp0"
set BOATRACE_LAN=1
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
