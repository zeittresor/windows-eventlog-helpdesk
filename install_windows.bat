@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "APP_NAME=Windows EventLog Helpdesk"
set "APP_VERSION=0.1.0"
set "PY_MIN=3.10"

for /F "delims=" %%E in ('echo prompt $E^| cmd') do set "ESC=%%E"
set "RESET=!ESC![0m"
set "CYAN=!ESC![96m"
set "BLUE=!ESC![94m"
set "GREEN=!ESC![92m"
set "YELLOW=!ESC![93m"
set "RED=!ESC![91m"
set "GRAY=!ESC![90m"

>nul 2>&1 reg add HKCU\Console /v VirtualTerminalLevel /t REG_DWORD /d 1 /f
if not exist "install_logs" mkdir "install_logs" >nul 2>&1
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%T"
set "LOG_FILE=%CD%\install_logs\install_!STAMP!.log"

call :banner
call :info "Installation log: !LOG_FILE!"

if /I "%~1"=="--build-wheelhouse" goto :build_wheelhouse

call :step "Checking Python launcher"
where py >nul 2>&1
if errorlevel 1 (
    call :error "Python launcher 'py' was not found. Install 64-bit Python 3.10 or newer and enable the launcher."
    goto :failed
)

for /f "usebackq delims=" %%V in (`py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2^>nul`) do set "PY_VERSION=%%V"
if not defined PY_VERSION (
    call :error "No usable Python 3 installation was found."
    goto :failed
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    call :error "Python 3.10 or newer is required. Detected !PY_VERSION!."
    goto :failed
)
call :ok "Python !PY_VERSION! detected"

call :step "Creating or repairing the project-local virtual environment"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv ".venv" >>"!LOG_FILE!" 2>&1
    if errorlevel 1 (
        call :error "Could not create .venv. See the installation log."
        goto :failed
    )
) else (
    call :info "Existing .venv found; performing an idempotent repair/install pass."
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
set "VENV_PYW=%CD%\.venv\Scripts\pythonw.exe"

call :step "Updating pip, setuptools and wheel"
"!VENV_PY!" -m pip install --upgrade pip setuptools wheel >>"!LOG_FILE!" 2>&1
if errorlevel 1 (
    call :warn "Tooling update failed; continuing with the installed pip version."
)

set "HAS_WHEELS=0"
for %%F in ("wheelhouse\*.whl") do if exist "%%~fF" set "HAS_WHEELS=1"
if "!HAS_WHEELS!"=="1" (
    call :step "Installing dependencies from the local wheelhouse (offline mode)"
    "!VENV_PY!" -m pip install --no-index --find-links "%CD%\wheelhouse" -r requirements.txt >>"!LOG_FILE!" 2>&1
) else (
    call :step "No wheelhouse packages found; installing dependencies from the configured Python package index"
    "!VENV_PY!" -m pip install -r requirements.txt >>"!LOG_FILE!" 2>&1
)
if errorlevel 1 (
    call :error "Dependency installation failed. See !LOG_FILE!"
    goto :failed
)

call :step "Running application import and parser self-checks"
"!VENV_PY!" -m compileall -q app.py eventlog_helpdesk >>"!LOG_FILE!" 2>&1
if errorlevel 1 (
    call :error "Python source validation failed. See !LOG_FILE!"
    goto :failed
)
"!VENV_PY!" -m unittest discover -q >>"!LOG_FILE!" 2>&1
if errorlevel 1 (
    call :error "Built-in parser/export tests failed. See !LOG_FILE!"
    goto :failed
)

call :ok "%APP_NAME% v%APP_VERSION% installed successfully"
echo.
call :info "The application will start automatically in 10 seconds. Press N to cancel the start."
choice /C YN /N /T 10 /D Y /M "Start %APP_NAME% v%APP_VERSION% now? [Y/n] "
if errorlevel 2 goto :done
call :step "Starting %APP_NAME% v%APP_VERSION%"
start "" "!VENV_PYW!" "%CD%\app.py"
goto :done

:build_wheelhouse
call :step "Building an offline wheelhouse for %APP_NAME% v%APP_VERSION%"
where py >nul 2>&1
if errorlevel 1 (
    call :error "Python launcher 'py' was not found."
    goto :failed
)
if not exist "wheelhouse" mkdir "wheelhouse"
py -3 -m pip download --dest "%CD%\wheelhouse" -r requirements.txt >>"!LOG_FILE!" 2>&1
if errorlevel 1 (
    call :error "Wheelhouse build failed. See !LOG_FILE!"
    goto :failed
)
call :ok "Offline wheelhouse completed"
goto :done

:banner
echo !CYAN!============================================================!RESET!
echo !CYAN!  %APP_NAME%  v%APP_VERSION%!RESET!
echo !BLUE!  Windows / PyQt6 Installer - online and offline capable!RESET!
echo !CYAN!============================================================!RESET!
echo.
exit /b 0

:step
echo !BLUE![STEP]!RESET! %~1
exit /b 0

:info
echo !CYAN![INFO]!RESET! %~1
exit /b 0

:ok
echo !GREEN![ OK ]!RESET! %~1
exit /b 0

:warn
echo !YELLOW![WARN]!RESET! %~1
exit /b 0

:error
echo !RED![FAIL]!RESET! %~1
exit /b 0

:failed
echo.
call :error "Installation did not complete."
call :info "Review: !LOG_FILE!"
pause
exit /b 1

:done
echo.
call :ok "Finished. Version: %APP_VERSION%"
call :info "Original source / updates: github.com/zeittresor/windows-eventlog-helpdesk"
timeout /t 2 /nobreak >nul
exit /b 0
