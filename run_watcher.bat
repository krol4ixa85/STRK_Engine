@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================================
echo   STRK Engine - Watcher (threshold check)
echo ============================================================
echo.

if not exist "config\config.env" (
    echo [FAIL] config\config.env not found
    echo Run check_config.bat first.
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

echo.
echo Running watcher_thresholds.py --once ...
echo ------------------------------------------------------------

python scripts\collectors\watcher_thresholds.py --once

set EXIT_CODE=%errorlevel%
echo ------------------------------------------------------------
echo.

if %EXIT_CODE% NEQ 0 (
    echo [FAIL] Exit code %EXIT_CODE%
) else (
    echo [OK] Watcher check complete
)

echo.
echo Press any key to close...
pause >nul
exit /b %EXIT_CODE%
