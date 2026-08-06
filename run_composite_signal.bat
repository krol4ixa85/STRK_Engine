@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================================
echo   STRK Composite Signal v2
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

echo Running composite_detector_v2.py...
echo ------------------------------------------------------------

python scripts\detectors\composite_detector_v2.py

set EXIT_CODE=%errorlevel%
echo ------------------------------------------------------------
echo.

if %EXIT_CODE% NEQ 0 (
    echo [FAIL] Exit code %EXIT_CODE%
) else (
    echo [OK] Signal saved to data\cache\composite_signal_v2.json
    echo If signal is non-neutral, Telegram alert was sent.
)

echo.
echo Press any key to close...
pause >nul
exit /b %EXIT_CODE%
