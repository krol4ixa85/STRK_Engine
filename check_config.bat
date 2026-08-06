@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================================
echo   STRK Engine - Configuration Check
echo ============================================================
echo.

REM ===== 1. Python =====
echo [1/5] Checking Python...
where python >nul 2>nul
if errorlevel 1 (
    echo   [FAIL] Python not found in PATH
    echo   Install Python 3.10+ from https://python.org
    echo   Make sure to check "Add Python to PATH" during install
    goto :end_fail
)
python --version
echo   [OK]
echo.

REM ===== 2. config.env =====
echo [2/5] Checking config\config.env...
if not exist "config\config.env" (
    echo   [FAIL] File config\config.env NOT FOUND
    echo.
    echo   HOW TO FIX:
    echo   1. Open folder "config"
    echo   2. Find file "config.env.example"
    echo   3. Make a COPY of it, rename copy to "config.env" ^(no .example^)
    echo   4. Open config.env in Notepad
    echo   5. Fill in your API keys:
    echo        ETHERSCAN_API_KEY=your_key_here
    echo        STARKSCAN_API_KEY=your_key_here
    echo        TELEGRAM_BOT_TOKEN=your_bot_token
    echo        TELEGRAM_CHAT_ID=your_chat_id
    echo   6. Save the file
    echo   7. Run check_config.bat again
    goto :end_fail
)
echo   [OK] config\config.env found
echo.

REM ===== 3. Load config and check keys =====
echo [3/5] Loading API keys from config.env...
for /f "usebackq tokens=1,* delims==" %%a in ("config\config.env") do (
    set "line=%%a"
    if not "!line:~0,1!"=="#" (
        if not "%%a"=="" (
            set "%%a=%%b"
        )
    )
)

if "%ETHERSCAN_API_KEY%"=="" (
    echo   [FAIL] ETHERSCAN_API_KEY is empty in config.env
    echo   Get free key at: https://etherscan.io/apis
    goto :end_fail
)
echo   [OK] ETHERSCAN_API_KEY loaded

if "%STARKSCAN_API_KEY%"=="" (
    echo   [FAIL] STARKSCAN_API_KEY is empty in config.env
    echo   Get free key at: https://starkscan.co/api
    goto :end_fail
)
echo   [OK] STARKSCAN_API_KEY loaded
echo.

REM ===== 4. Seeds file =====
echo [4/5] Checking data\seeds\flow_seeds.json...
if not exist "data\seeds\flow_seeds.json" (
    echo   [FAIL] flow_seeds.json not found
    goto :end_fail
)
echo   [OK] found
echo.

REM ===== 5. Test JSON reading =====
echo [5/5] Testing JSON read ^(UTF-8^)...
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set STRICT_NO_TRADING=true
python scripts\collectors\flow_eth.py --dry-run
if errorlevel 1 (
    echo   [FAIL] Cannot read flow_seeds.json
    echo   Possible encoding problem. Check the file was not corrupted.
    goto :end_fail
)
echo.

echo ============================================================
echo   [OK] All checks passed. You can run run_orchestrator.bat now
echo ============================================================
echo.
pause
exit /b 0

:end_fail
echo.
echo ============================================================
echo   [FAIL] Some checks failed. Read messages above.
echo ============================================================
echo.
pause
exit /b 1
