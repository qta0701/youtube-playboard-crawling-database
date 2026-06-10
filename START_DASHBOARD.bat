@echo off
chcp 65001 >nul

:: Find an available port starting from 8501
set PORT=8501
:find_port
netstat -ano | findstr /R /C:"[.:]%PORT% " >nul 2>&1
if %errorlevel% equ 0 (
    echo [WARNING] Port %PORT% is in use. Searching for next port...
    set /a PORT=%PORT%+1
    if %PORT% gtr 8600 (
        echo [ERROR] No available port found between 8501 and 8600.
        pause
        exit /b 1
    )
    goto find_port
)

title YouTube DB Dashboard & Crawler (Port %PORT%)
cd /d "%~dp0"

echo ===================================================
echo   YouTube Pro Dashboard - Port %PORT%
echo ===================================================
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
        echo [WARNING] python3.14-64.exe not found. Using default 'python' fallback.
    )
)

:: Check if virtual environment exists
if not exist venv (
    echo Virtual environment not found. Creating...
    "%PYTHON_CMD%" -m venv venv
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

:: Check if requirements are installed (including undetected_chromedriver, selenium_stealth, streamlit, plotly)
python -c "import undetected_chromedriver, selenium_stealth, streamlit, plotly" >nul 2>&1
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

:: Run the Streamlit application with fallback support
echo [INFO] Starting Streamlit Application on Port %PORT%...
streamlit run app.py --server.port %PORT% 2>nul
if %errorlevel% neq 0 (
    echo [WARNING] Direct 'streamlit' command failed. Trying python module call...
    python -m streamlit run app.py --server.port %PORT% 2>nul
    if %errorlevel% neq 0 (
        echo [WARNING] Venv Python streamlit failed. Trying global python...
        deactivate 2>nul
        "%PYTHON_CMD%" -m streamlit run app.py --server.port %PORT%
        if %errorlevel% neq 0 (
            echo ERROR: Failed to run Streamlit app. Please ensure streamlit is installed.
            pause
            exit /b 1
        )
    )
)
pause

