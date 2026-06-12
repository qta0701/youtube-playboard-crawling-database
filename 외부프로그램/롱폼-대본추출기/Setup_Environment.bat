@echo off
setlocal enabledelayedexpansion

REM UTF-8 인코딩 설정
chcp 65001 >nul 2>&1

REM 윈도우 제목 설정
title YouTube Shorts 자막 추출기 - 환경 설정

echo ========================================
echo  YouTube Shorts Subtitle Extractor
echo  Environment Setup Script
echo ========================================
echo  This script will:
echo  - Check Python installation
echo  - Upgrade pip
echo  - Install required packages
echo  - Verify environment
echo ========================================
echo.

REM 현재 배치 파일 디렉토리로 이동
pushd "%~dp0"

echo [1/5] Current directory: %CD%
echo.

REM Python 설치 확인
echo [2/5] Checking Python installation...
where python >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo [OK] Python is installed
    python --version
    echo.
) else (
    echo [ERROR] Python not found
    echo.
    echo Please install Python:
    echo 1. Download from https://python.org
    echo 2. Check "Add Python to PATH" during installation
    echo 3. Run this script again after installation
    echo.
    pause
    goto :error_exit
)

REM pip 버전 확인
echo [3/5] Checking pip version...
python -m pip --version 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] pip is not working properly
    echo [INFO] Attempting to install/repair pip...
    echo.

    REM ensurepip를 사용하여 pip 설치
    python -m ensurepip --default-pip
    if %ERRORLEVEL% NEQ 0 (
        echo [INFO] Trying alternative method: downloading get-pip.py...

        REM get-pip.py 다운로드 및 실행
        python -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', 'get-pip.py')"
        if exist "get-pip.py" (
            python get-pip.py
            del get-pip.py
        )
    )

    REM 재확인
    echo.
    echo Verifying pip installation...
    python -m pip --version
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to install pip
        echo.
        echo Please manually install pip:
        echo 1. Download get-pip.py from https://bootstrap.pypa.io/get-pip.py
        echo 2. Run: python get-pip.py
        echo 3. Or reinstall Python with pip included
        echo.
        pause
        goto :error_exit
    )
    echo [OK] pip installed successfully
    echo.
) else (
    echo [OK] pip is working
    echo.
)

REM pip 업그레이드
echo Upgrading pip...
python -m pip install --upgrade pip 2>nul
if %ERRORLEVEL% == 0 (
    echo [OK] pip upgraded successfully
    echo.
) else (
    echo [WARNING] pip upgrade failed, continuing anyway...
    echo.
)

REM requirements.txt 존재 확인
echo [4/5] Checking requirements.txt...
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt file not found
    echo Current directory: %CD%
    echo.
    pause
    goto :error_exit
)
echo [OK] requirements.txt found
echo.

REM 패키지 설치
echo Installing required packages...
echo.
echo Packages to install:
type requirements.txt
echo.
echo Starting package installation...
echo This may take a few minutes...
echo.
echo ==========================================
echo Installation output:
echo ==========================================

REM 에러 출력을 포함하여 실행 (상세 출력)
python -m pip install -r requirements.txt --no-cache-dir --disable-pip-version-check --verbose
set INSTALL_ERROR=%ERRORLEVEL%

echo ==========================================
echo.

if %INSTALL_ERROR% == 0 (
    echo [OK] All packages installed successfully
    echo.
) else (
    echo [ERROR] Package installation failed (Error code: %INSTALL_ERROR%)
    echo.
    echo Troubleshooting:
    echo 1. Check internet connection
    echo 2. Run as administrator (Right-click -^> Run as administrator)
    echo 3. Check if Python has SSL support: python -c "import ssl; print(ssl.OPENSSL_VERSION)"
    echo 4. Try manual install: python -m pip install aiohttp gspread selenium keyboard
    echo 5. Check if antivirus/firewall is blocking installation
    echo 6. If using corporate network, check proxy settings
    echo.
    pause
    goto :error_exit
)

REM 환경 검증
echo [5/5] Verifying environment...
echo.
if exist "Check_Environment.py" (
    python Check_Environment.py
    set CHECK_ERROR=!ERRORLEVEL!
    if !CHECK_ERROR! == 0 (
        echo.
        echo ========================================
        echo  [SUCCESS] Environment setup completed!
        echo ========================================
        echo  You can now run:
        echo  - GUI_Start.bat (GUI version)
        echo ========================================
    ) else (
        echo.
        echo [WARNING] Some issues found during verification
        echo Please check the messages above
    )
) else (
    echo [WARNING] Check_Environment.py not found
    echo Packages installed but verification skipped
)

echo.
echo Setup complete! Press any key to exit...
pause >nul
goto :end

:error_exit
echo.
echo ========================================
echo  [FAILED] Environment setup error
echo ========================================
echo Please check the error messages above
echo and try again after fixing the issues.
echo.
pause
exit /b 1

:end
popd
endlocal