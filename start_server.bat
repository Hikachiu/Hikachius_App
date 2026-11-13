@echo off
rem Boatrace app server for home Wi-Fi (phones on the same Wi-Fi can open it).
rem Double-click to start. Close this window to stop.
rem First run: creates .venv and installs packages automatically.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run

echo [setup] .venv not found. Creating the Python environment...
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython

%PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto nopython

echo [setup] Installing packages (first time only)...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail
echo [setup] Done.

:run
set BOATRACE_LAN=1
set PYTHONUTF8=1
echo Starting server... open http://localhost:8000/settings on this PC for the phone QR code.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
exit /b

:nopython
echo.
echo [error] Python was not found.
echo   Install Python from https://www.python.org/downloads/
echo   and check "Add python.exe to PATH" on the first screen.
echo   Then open a NEW window and run this file again.
pause
exit /b 1

:pipfail
echo.
echo [error] Package install failed. Check the internet connection and run this file again.
echo   To retry from scratch, delete the .venv folder first.
pause
exit /b 1
