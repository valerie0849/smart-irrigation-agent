@echo off
setlocal EnableDelayedExpansion
title Irrigation System v3

echo ============================================================
echo   Smart Irrigation Planner v3
echo   DeepSeek-V4-Pro ^| Knowledge Forest ^| Plan Generator
echo ============================================================
echo.

cd /d "%~dp0"

echo [1/3] Checking port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING 2^>nul') do (
    echo   Closing PID %%a...
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=2 delims=," %%a in ('tasklist /FI "WINDOWTITLE eq irrigation-backend*" /FO CSV /NH 2^>nul') do (
    set "pid=%%~a"
    if not "!pid!"=="" (
        echo   Closing backend PID !pid!...
        taskkill /F /PID !pid! >nul 2>&1
    )
)
timeout /t 2 /nobreak >nul

echo.
echo [2/3] Starting backend server...
echo   Local: http://localhost:8000
echo   Docs:  http://localhost:8000/docs
echo.
start "irrigation-backend" cmd /c "python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"

echo [3/3] Waiting for server...
:wait
ping -n 3 127.0.0.1 >nul
powershell -Command "$r=0; try { $r = (Invoke-WebRequest http://127.0.0.1:8000/health -TimeoutSec 3 -UseBasicParsing).StatusCode } catch {}; if($r -ne 200){ exit 1 }" 2>nul
if %errorlevel% neq 0 goto wait

:: Get LAN IP
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4" 2^>nul') do (
    for /f "tokens=1" %%b in ("%%a") do (
        if not "%%b"=="" (
            if not "%%b"=="127.0.0.1" (
                set LAN_IP=%%b
                goto :found_ip
            )
        )
    )
)
:found_ip

echo   Opening browser...
start http://localhost:8000

echo.
if defined LAN_IP (
    echo   LAN Access: http://!LAN_IP!:8000
    echo   Share this URL to others on same network
)

echo.
echo ============================================================
echo   Server started! Press any key to close this window.
echo ============================================================
pause >nul
