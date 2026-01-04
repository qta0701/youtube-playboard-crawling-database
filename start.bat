@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: Check if virtual environment exists
if not exist venv (
    echo Virtual environment not found. Creating...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment
        echo Please ensure Python 3.8+ is installed
        pause
        exit /b 1
    )
    echo Virtual environment created successfully
)

:: Activate virtual environment
call venv\Scripts\activate.bat

:: Check if requirements are installed
python -c "import flask" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing required packages...
    python -m pip install --upgrade pip >nul 2>&1
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo ERROR: Failed to install packages
        pause
        exit /b 1
    )
    echo Packages installed successfully
)

:: Create necessary directories
if not exist output mkdir output
if not exist output\transcripts mkdir output\transcripts
if not exist logs mkdir logs
if not exist google_service_key mkdir google_service_key

:: Run the application
python run.py
pause
