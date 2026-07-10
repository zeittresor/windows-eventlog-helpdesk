@echo off
setlocal
cd /d "%~dp0"
set "APP_VERSION=0.1.0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo [INFO] Windows EventLog Helpdesk v%APP_VERSION% is not installed yet.
    call install_windows.bat
    exit /b %errorlevel%
)
start "" ".venv\Scripts\pythonw.exe" "%CD%\app.py"
exit /b 0
