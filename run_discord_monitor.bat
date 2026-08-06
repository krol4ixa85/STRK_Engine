@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================================
echo   STRK Engine - Discord Alert Monitor
echo ============================================================
echo.

if not exist "config\config.env" (
    echo [FAIL] config\config.env not found
    pause
    exit /b 1
)

for /f "usebackq tokens=1,* delims==" %%a in ("config\config.env") do (
    set "line=%%a"
    if not "!line:~0,1!"=="#" (
        if not "%%a"=="" (
            set "%%a=%%b"
        )
    )
)

if "%DISCORD_BOT_TOKEN%"=="" (
    echo [FAIL] DISCORD_BOT_TOKEN empty. Skip Discord module.
    pause
    exit /b 1
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set STRICT_NO_TRADING=true

echo Fetching new Discord alerts...
echo ------------------------------------------------------------

python scripts\collectors\discord_monitor.py --once

set EXIT_CODE=%errorlevel%
echo ------------------------------------------------------------
echo.

if %EXIT_CODE% NEQ 0 (
    echo [FAIL] Exit code %EXIT_CODE%
) else (
    echo [OK] Discord check complete
)

echo.
echo Press any key to close...
pause >nul
exit /b %EXIT_CODE%
