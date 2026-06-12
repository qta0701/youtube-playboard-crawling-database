@echo off
setlocal

REM UTF-8 인코딩 설정
chcp 65001 >nul 2>&1

title Repair pip

echo ========================================
echo  pip Repair Tool
echo ========================================
echo.

pushd "%~dp0"

echo [1] Checking Python...
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found
    echo Please install Python first
    pause
    exit /b 1
)
echo [OK] Python found
python --version
echo.

echo [2] Attempting to repair pip...
echo.

echo Method 1: Using ensurepip...
python -m ensurepip --default-pip
if %ERRORLEVEL% == 0 (
    echo [OK] pip installed via ensurepip
    goto :verify
)
echo [INFO] ensurepip failed, trying alternative method...
echo.

echo Method 2: Downloading get-pip.py...
echo This will download from https://bootstrap.pypa.io/get-pip.py
python -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', 'get-pip.py'); print('[OK] Downloaded get-pip.py')"
if exist "get-pip.py" (
    echo Running get-pip.py...
    python get-pip.py
    set INSTALL_ERROR=%ERRORLEVEL%
    del get-pip.py
    if %INSTALL_ERROR% == 0 (
        echo [OK] pip installed via get-pip.py
        goto :verify
    )
)
echo.

echo Method 3: Manual upgrade...
python -m pip install --upgrade --force-reinstall pip
if %ERRORLEVEL% == 0 (
    echo [OK] pip upgraded
    goto :verify
)
echo.

:verify
echo.
echo ========================================
echo [3] Verifying pip installation...
echo ========================================
python -m pip --version
if %ERRORLEVEL% == 0 (
    echo.
    echo ========================================
    echo  [SUCCESS] pip is now working!
    echo ========================================
    echo.
    echo You can now run Setup_Environment.bat
) else (
    echo.
    echo ========================================
    echo  [FAILED] pip repair failed
    echo ========================================
    echo.
    echo Manual solutions:
    echo 1. Reinstall Python from https://python.org
    echo    - Make sure to check "pip" during installation
    echo    - Check "Add Python to PATH"
    echo.
    echo 2. Or manually download and run:
    echo    https://bootstrap.pypa.io/get-pip.py
)

echo.
pause
popd
endlocal
