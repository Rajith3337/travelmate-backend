@echo off
:: ════════════════════════════════════════════════════════
::  TravelMate Backend — Windows Launcher
::  Run from: be\  (the folder containing main.py)
:: ════════════════════════════════════════════════════════

setlocal enabledelayedexpansion
title TravelMate Backend

echo.
echo  ╔══════════════════════════════════════╗
echo  ║     TravelMate Backend Launcher      ║
echo  ╚══════════════════════════════════════╝
echo.

:: ── Check .env exists ────────────────────────────────────
if not exist ".env" (
    echo  [WARN] .env not found in current directory.
    if exist ".env.template" (
        echo  [INFO] Copying .env.template to .env ...
        copy ".env.template" ".env" >nul
        echo  [INFO] .env created. Edit it with your real values before continuing.
    ) else (
        echo  [ERROR] No .env or .env.template found.
        echo  [INFO]  Create be\be\.env with your DATABASE_URL and API keys.
    )
    echo.
)

:: ── Check Python ─────────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found. Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

:: ── Activate venv if it exists ───────────────────────────
if exist "..\..\venv\Scripts\activate.bat" (
    echo  [INFO] Activating virtualenv ...
    call "..\..\venv\Scripts\activate.bat"
) else if exist "..\venv\Scripts\activate.bat" (
    call "..\venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
) else (
    echo  [WARN] No venv found — using system Python.
)

:: ── Install / check dependencies ─────────────────────────
echo  [INFO] Checking dependencies (aiosqlite for local fallback) ...
python -m pip install aiosqlite --quiet 2>nul

:: ── Show DB target ───────────────────────────────────────
echo.
echo  [INFO] Reading DATABASE_URL from .env ...
for /f "tokens=1,* delims==" %%a in (.env) do (
    if "%%a"=="DATABASE_URL" (
        set DB_LINE=%%b
    )
)
if defined DB_LINE (
    echo  [DB]   !DB_LINE!
) else (
    echo  [WARN] DATABASE_URL not found in .env
)
echo.
echo  [INFO] If the remote DB is unreachable from your PC, the backend will
echo  [INFO] automatically fall back to a LOCAL SQLite database.
echo  [INFO] This means the app works fully without a network connection.
echo.

:: ── Launch uvicorn ───────────────────────────────────────
echo  [INFO] Starting uvicorn on http://localhost:8000 ...
echo  [INFO] Press Ctrl+C to stop.
echo.
uvicorn main:app --host 0.0.0.0 --port 8000 --reload --reload-dir .

pause
