@echo off
chcp 65001 >nul
title Package Installation - YouTube Crawler
cd /d "%~dp0"

echo ========================================
echo YouTube Crawler Pro - Installation
echo ========================================
echo.

:: Check Python installation
echo [1/8] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH
    echo Please install Python 3.9+ from https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version
echo.

:: Remove old venv if corrupted
if exist venv (
    echo [2/8] Checking existing virtual environment...
    call venv\Scripts\activate.bat 2>nul
    if %errorlevel% neq 0 (
        echo [WARNING] Virtual environment is corrupted. Removing...
        rd /s /q venv
    ) else (
        echo [INFO] Virtual environment exists and is valid
        call venv\Scripts\deactivate.bat 2>nul
    )
) else (
    echo [2/8] No existing virtual environment found
)
echo.

:: Create virtual environment
echo [3/8] Creating virtual environment...
if exist venv (
    echo [INFO] Virtual environment already exists. Skipping...
) else (
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
    echo [SUCCESS] Virtual environment created successfully
)
echo.

:: Activate virtual environment and upgrade pip
echo [4/8] Activating virtual environment and upgrading pip...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
if %errorlevel% neq 0 (
    echo [WARNING] Failed to upgrade pip, continuing anyway...
) else (
    echo [SUCCESS] pip, setuptools, and wheel upgraded
)
echo.

:: Install required packages
echo [5/8] Installing required packages from requirements.txt...
echo This may take several minutes on first installation...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install packages
    echo Please check your internet connection and try again
    pause
    exit /b 1
)
echo [SUCCESS] All packages installed successfully
echo.

:: Verify webdriver-manager
echo [6/8] Verifying webdriver-manager installation...
python -c "import webdriver_manager" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] webdriver-manager not found, installing...
    pip install webdriver-manager
    if %errorlevel% neq 0 (
        echo [WARNING] Failed to install webdriver-manager, continuing anyway...
    ) else (
        echo [SUCCESS] webdriver-manager installed
    )
) else (
    echo [INFO] webdriver-manager is already installed
)
echo.

:: Create necessary directories
echo [7/8] Creating necessary directories...
if not exist output mkdir output
if not exist output\transcripts mkdir output\transcripts
if not exist output\db mkdir output\db
if not exist logs mkdir logs
if not exist google_service_key mkdir google_service_key
if not exist static mkdir static
if not exist static\js mkdir static\js
if not exist static\css mkdir static\css
if not exist templates mkdir templates
if not exist modules mkdir modules
echo [SUCCESS] All directories created
echo.

:: Verify installation
echo [8/8] Verifying installation...
python -c "import flask, selenium, pandas, bs4, googleapiclient, youtube_transcript_api; print('[SUCCESS] All required packages are working correctly')"
if %errorlevel% neq 0 (
    echo [ERROR] Package verification failed
    echo Some packages may not be installed correctly
    pause
    exit /b 1
)
echo.

echo ========================================
echo Installation completed successfully!
echo ========================================
echo.
echo Installed packages:
echo - Flask 3.0.0 (Web framework)
echo - Selenium 4.15.2 (Web automation)
echo - Pandas 2.1.3 (Data processing)
echo - BeautifulSoup4 4.12.2 (HTML parsing)
echo - Google API Client 2.108.0 (YouTube Data API)
echo - YouTube Transcript API 0.6.1
echo - webdriver-manager 4.0.1 (Automatic ChromeDriver)
echo - And all dependencies
echo.
echo ========================================
echo You can now run:
echo ========================================
echo - START_DASHBOARD.bat  (Dashboard on port 5001)
echo - start.bat            (Main crawler application)
echo.
echo Note: ChromeDriver will be automatically downloaded on first run
echo.
pause
