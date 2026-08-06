@echo off
chcp 65001 >nul

cd /d "%~dp0"

echo ============================================================
echo   STRK Engine - Wallet Registry Manager
echo ============================================================
echo.
echo Manage which wallets are monitored.
echo Add: new addresses to watch (whale_monitor will pick up)
echo Remove: stop watching an address
echo List: see all currently monitored addresses
echo.

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

python scripts\wallet_registry.py

echo.
echo Press any key to close...
pause >nul
