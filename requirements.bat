@echo off
chcp 65001 >nul
title Package Installation - YouTube Crawler
cd /d "%~dp0"

echo ========================================
echo YouTube Crawler Pro - Installation
echo ========================================
echo.

:: Find python3.14-64.exe dynamically based on rules.md
set "PYTHON_CMD=python"
if exist "%USERPROFILE%\AppData\Local\Python\bin\python3.14-64.exe" (
    set "PYTHON_CMD=%USERPROFILE%\AppData\Local\Python\bin\python3.14-64.exe"
    echo [INFO] Found Python 3.14 at AppData: %PYTHON_CMD%
) else (
    where python3.14-64.exe >nul 2>&1
    if %errorlevel% equ 0 (
        set "PYTHON_CMD=python3.14-64.exe"
        echo [INFO] Found Python 3.14 in PATH: %PYTHON_CMD%
    ) else (
        echo [WARNING] python3.14-64.exe not found. Using default 'python' command.
    )
)

:: Check Python installation
echo [1/8] Checking Python installation...
"%PYTHON_CMD%" --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH
    echo Please install Python 3.14 from standard channels.
    pause
    exit /b 1
)
"%PYTHON_CMD%" --version
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
    "%PYTHON_CMD%" -m venv venv
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
if %errorlevel% equ 0 goto install_success

:: pip가 캐시 에러 등으로 non-zero를 리턴했어도, 실제 패키지가 정상 임포트되는지 더블 체크하여 예외를 보완합니다.
python -c "import selenium, pandas, bs4, googleapiclient, youtube_transcript_api, undetected_chromedriver, selenium_stealth, streamlit, plotly" >nul 2>&1
if %errorlevel% equ 0 goto install_success

echo [ERROR] Failed to install packages
echo Please check your internet connection and try again
pause
exit /b 1

:install_success
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
if not exist modules mkdir modules
echo [SUCCESS] All directories created
echo.

:: Verify installation
echo [8/8] Verifying installation...
python -c "import selenium, pandas, bs4, googleapiclient, youtube_transcript_api, undetected_chromedriver, selenium_stealth, streamlit, plotly; print('[SUCCESS] All required packages are working correctly')"
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
echo - Selenium 4.15.2+ (Web automation)
echo - Pandas 2.2.3+ (Data processing)
echo - BeautifulSoup4 4.12.2+ (HTML parsing)
echo - Google API Client 2.108.0+ (YouTube Data API)
echo - YouTube Transcript API 0.6.1+
echo - webdriver-manager 4.0.1+ (Automatic ChromeDriver)
echo - selenium-stealth (Chrome Bot Detection Avoidance)
echo - undetected-chromedriver 3.5.5+ (Legacy Bot Detection Avoidance)
echo - Streamlit 1.58.0+ (Interactive App Dashboard)
echo - Plotly 5.18.0+ (Interactive Charts Library)
echo - And all dependencies
echo.
echo ========================================
echo You can now run:
echo ========================================
echo - START_DASHBOARD.bat  (Dashboard & Crawler on port 8501)
echo.
echo Note: ChromeDriver will be automatically downloaded on first run
echo.
pause
