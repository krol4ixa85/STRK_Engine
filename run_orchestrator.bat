@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================================
echo   STRK Engine - Orchestrator
echo ============================================================
echo.

REM Check config.env exists
if not exist "config\config.env" (
    echo [FAIL] config\config.env not found
    echo Run check_config.bat first to see what to do.
    echo.
    pause
    exit /b 1
)

REM Load env vars from config.env
echo Loading config.env...
for /f "usebackq tokens=1,* delims==" %%a in ("config\config.env") do (
    set "line=%%a"
    if not "!line:~0,1!"=="#" (
        if not "%%a"=="" (
            set "%%a=%%b"
        )
    )
)

REM Force UTF-8 and safety
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set STRICT_NO_TRADING=true

echo.
echo Running orchestrator.py...
echo ------------------------------------------------------------

python scripts\orchestrator.py

set EXIT_CODE=%errorlevel%
echo ------------------------------------------------------------
echo.

if %EXIT_CODE% NEQ 0 (
    echo [FAIL] Script exited with code %EXIT_CODE%
    echo See errors above.
) else (
    echo [OK] Done. Check data\cache\agent_input.json
)

echo.
echo Press any key to close window...
pause >nul
exit /b %EXIT_CODE%
