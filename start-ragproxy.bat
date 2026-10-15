@echo off
title RAG Proxy One-Click Launcher
cd /d "%~dp0"

echo ============================================
echo   RAG Proxy One-Click Launcher
echo ============================================
echo.

echo [1/3] Checking backend dependencies...
python -c "import fastapi, uvicorn, yaml, httpx" >nul 2>&1
if errorlevel 1 (
    echo     Dependencies missing, installing...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo     Install failed. Check Python environment.
        pause
        exit /b 1
    )
)

echo [2/3] Starting RAG Proxy...
set RAGPROXY_SECRET_KEY=ragproxy123
start "RAG Proxy Server" cmd /k "cd /d ""%~dp0"" && python -m ragproxy"

echo [3/3] Opening browser...
timeout /t 4 /nobreak >nul
start http://127.0.0.1:8100

echo.
echo Done! Open http://127.0.0.1:8100 in browser.
echo Password: ragproxy123
echo Close the black window to stop the service.
echo.
pause
