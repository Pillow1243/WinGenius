@echo off
REM ─────────────────────────────────────────────────────────────
REM  WinGenius — one-click Windows build (exe + optional installer)
REM ─────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0\.."

if not exist ".venv" (
  echo [1/4] creating virtual environment...
  python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error

echo [2/4] installing build requirements...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements-build.txt || goto :error

echo [3/4] building exe...
python packaging\build_exe.py %* || goto :error

echo [4/4] done. Look inside the dist folder.
pause
exit /b 0

:error
echo.
echo Build failed — see the messages above.
pause
exit /b 1
