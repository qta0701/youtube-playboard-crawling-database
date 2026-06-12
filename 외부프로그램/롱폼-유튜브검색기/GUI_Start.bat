@echo off
chcp 65001 > nul
title YouTube Search GUI

REM Create logs directory if not exists
if not exist logs mkdir logs

REM Generate log filename with timestamp
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%a-%%b-%%c)
for /f "tokens=1-2 delims=/: " %%a in ("%TIME%") do (set mytime=%%a-%%b)
set mytime=%mytime: =0%
set logfile=logs\debug_%mydate%_%mytime%.log

echo ================================================ > "%logfile%"
echo YouTube Search and Google Sheets Integration >> "%logfile%"
echo Start Time: %date% %time% >> "%logfile%"
echo ================================================ >> "%logfile%"
echo. >> "%logfile%"

echo ================================================
echo YouTube Search GUI Starting...
echo ================================================
echo.
echo Log file: %logfile%
echo.

REM Run Python and capture both stdout and stderr
python Main_Search.py >> "%logfile%" 2>&1
set ERROR_CODE=%errorlevel%

if %ERROR_CODE% neq 0 (
    echo. >> "%logfile%"
    echo ================================================ >> "%logfile%"
    echo Error occurred. Error level: %ERROR_CODE% >> "%logfile%"
    echo ================================================ >> "%logfile%"
    echo.
    echo ================================================
    echo Error occurred. Error level: %ERROR_CODE%
    echo Please check log file: %logfile%
    echo ================================================
    echo.
    type "%logfile%"
    echo.
    pause
) else (
    echo. >> "%logfile%"
    echo ================================================ >> "%logfile%"
    echo Program finished successfully >> "%logfile%"
    echo End Time: %date% %time% >> "%logfile%"
    echo ================================================ >> "%logfile%"
)
