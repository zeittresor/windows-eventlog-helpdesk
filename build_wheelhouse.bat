@echo off
setlocal
cd /d "%~dp0"
call install_windows.bat --build-wheelhouse
exit /b %errorlevel%
