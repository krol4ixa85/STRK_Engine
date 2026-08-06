@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================================
echo   STRK Engine - Discord Bot Connection Test
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
    echo [FAIL] DISCORD_BOT_TOKEN is empty in config.env
    echo See docs\DISCORD_SETUP.md for how to get a bot token.
    pause
    exit /b 1
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo Testing connection to Discord channel %DISCORD_CHANNEL_ID%...
echo ------------------------------------------------------------

python scripts\collectors\discord_monitor.py --test

set EXIT_CODE=%errorlevel%
echo ------------------------------------------------------------
echo.

if %EXIT_CODE% NEQ 0 (
    echo [FAIL] Connection test failed. See errors above.
    echo Common issues:
    echo   401 = Token invalid or bot removed from server
    echo   403 = Bot lacks READ_MESSAGES permission
    echo   404 = Channel ID wrong or bot not member
) else (
    echo [OK] Discord bot connected successfully
    echo Now you can run run_discord_monitor.bat to fetch alerts
)

echo.
pause
exit /b %EXIT_CODE%
