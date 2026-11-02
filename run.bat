@echo off
REM اجرای WinGenius از سورس (بدون ساخت exe)
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py %*
) else (
    python main.py %*
)
if errorlevel 1 pause
