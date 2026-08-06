@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================================
echo   STRK Whale Monitor (real-time large transfers)
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

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set STRICT_NO_TRADING=true

echo Checking last 35 minutes for large STRK transfers...
echo ------------------------------------------------------------

python scripts\collectors\whale_monitor.py --once --window 35

set EXIT_CODE=%errorlevel%
echo ------------------------------------------------------------
echo.
echo Press any key to close...
pause >nul
exit /b %EXIT_CODE%
