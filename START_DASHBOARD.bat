@echo off
chcp 65001 >nul
title YouTube DB Dashboard (Port 5001)
cd /d "%~dp0"

echo ===================================================
echo   YouTube DB Dashboard - Port 5001
echo   Zero-Cost ID Extraction Mode
echo ===================================================
echo.

REM Check virtual environment
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found.
    echo [INFO] Please run start.bat first to setup environment.
    pause
    exit /b 1
)

REM Activate virtual environment
echo [DEBUG] Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment
    pause
    exit /b 1
)
echo [DEBUG] Virtual environment activated successfully

REM Create required directories
echo [DEBUG] Creating required directories...
if not exist "output\db" mkdir "output\db"
echo [DEBUG] Directories ready

REM Check and install required packages
echo [DEBUG] Checking required packages...

echo [DEBUG] Checking Flask...
python -c "import flask" 2>nul
if errorlevel 1 (
    echo [INFO] Installing Flask...
    pip install flask
    if errorlevel 1 (
        echo [ERROR] Failed to install Flask
        pause
        exit /b 1
    )
) else (
    echo [DEBUG] Flask is already installed
)

echo [DEBUG] Checking google-api-python-client...
python -c "from googleapiclient.discovery import build" 2>nul
if errorlevel 1 (
    echo [INFO] Installing google-api-python-client...
    pip install google-api-python-client
    if errorlevel 1 (
        echo [ERROR] Failed to install google-api-python-client
        pause
        exit /b 1
    )
) else (
    echo [DEBUG] google-api-python-client is already installed
)

echo [DEBUG] Checking youtube-transcript-api...
python -c "from youtube_transcript_api import YouTubeTranscriptApi" 2>nul
if errorlevel 1 (
    echo [INFO] Installing youtube-transcript-api...
    pip install youtube-transcript-api
    if errorlevel 1 (
        echo [ERROR] Failed to install youtube-transcript-api
        pause
        exit /b 1
    )
) else (
    echo [DEBUG] youtube-transcript-api is already installed
)

echo [DEBUG] All required packages are ready

echo.
echo ===================================================
echo   Checking for existing Dashboard process
echo ===================================================

REM Kill existing Python processes on port 5001
echo [INFO] Checking for processes on port 5001...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5001 ^| findstr LISTENING') do (
    echo [INFO] Found process on port 5001 (PID: %%a^), terminating...
    taskkill /F /PID %%a >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Failed to terminate PID %%a
    ) else (
        echo [SUCCESS] Process %%a terminated
    )
)

REM Additional safety: kill any dashboard_app.py processes
echo [INFO] Checking for dashboard_app.py processes...
tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH 2>nul | findstr /I "dashboard_app.py" >nul
if not errorlevel 1 (
    echo [INFO] Terminating existing dashboard_app.py processes...
    taskkill /F /IM python.exe /FI "WINDOWTITLE eq *dashboard_app.py*" >nul 2>&1
)

REM Wait for port to be released
echo [INFO] Waiting for port 5001 to be released...
timeout /t 2 /nobreak >nul

echo.
echo ===================================================
echo   Cleaning up old logs
echo ===================================================
if exist logs\*.log del /q logs\*.log
if exist logs\*.txt del /q logs\*.txt
echo [INFO] Old logs deleted.

echo.
echo ===================================================
echo   Starting Dashboard Server
echo ===================================================
echo [INFO] Port: 5001
echo [INFO] URL: http://localhost:5001
echo [INFO] Log file: logs\log_START_DASHBOARD_*.log
echo.
echo [DEBUG] Opening browser in 3 seconds...
echo ===================================================
echo.

REM Open browser after 3 second delay (in background)
start /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:5001"

REM Start dashboard
python dashboard_app.py

echo.
echo ===================================================
echo [INFO] Dashboard stopped.
echo [INFO] Check logs folder for detailed logs
echo ===================================================
pause
