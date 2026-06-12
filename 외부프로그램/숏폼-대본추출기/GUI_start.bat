@echo off
setlocal

REM UTF-8 인코딩 설정
chcp 65001 >nul 2>&1

REM 윈도우 제목 설정
title YouTube Shorts Subtitle Extractor GUI

echo ========================================
echo  YouTube Shorts Subtitle Extractor GUI
echo ========================================
echo  Features:
echo  - Sheet selection and info check
echo  - Real-time Google Sheets update
echo  - Browser automation + HTTP fallback
echo  - Progress monitoring
echo ========================================
echo.

REM 현재 배치 파일 디렉토리로 이동
pushd "%~dp0"

REM Python 설치 확인
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found
    echo Please make sure Python is installed and added to PATH
    echo.
    echo If Python is not installed:
    echo 1. Run Setup_Environment.bat
    echo 2. Or install Python from https://python.org
    echo.
    pause
    goto :end
)

REM GUI_Extract.py 파일 존재 확인
if not exist "GUI_Extract.py" (
    echo [ERROR] GUI_Extract.py not found
    echo Current directory: %CD%
    echo.
    pause
    goto :end
)

REM Python 실행
echo [OK] Python found. Starting GUI...
echo.
python GUI_Extract.py
set GUI_ERROR=%ERRORLEVEL%

if %GUI_ERROR% NEQ 0 (
    echo.
    echo ========================================
    echo  [ERROR] GUI application error
    echo ========================================
    echo Error code: %GUI_ERROR%
    echo.
    echo Possible solutions:
    echo 1. Run Setup_Environment.bat to install dependencies
    echo 2. Check logs folder for error details
    echo 3. Make sure all required files are present
    echo.
)

:end
echo.
echo ========================================
echo  GUI application closed
echo ========================================
pause
popd
endlocal