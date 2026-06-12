@echo off
setlocal

title Python Diagnostic

echo ========================================
echo  Python Environment Diagnostic
echo ========================================
echo.

pushd "%~dp0"

echo [Test 1] Finding Python executable
echo ----------------------------------------
where python
set WHERE_ERROR=%ERRORLEVEL%
echo Error level: %WHERE_ERROR%
echo.

echo [Test 2] Python version command
echo ----------------------------------------
echo Running: python --version
python --version
set VERSION_ERROR=%ERRORLEVEL%
echo Error level: %VERSION_ERROR%
echo.

echo [Test 3] Python version via -V flag
echo ----------------------------------------
echo Running: python -V
python -V
echo.

echo [Test 4] Python executable path
echo ----------------------------------------
echo Running: python -c "import sys; print(sys.executable)"
python -c "import sys; print(sys.executable)"
set EXEC_ERROR=%ERRORLEVEL%
echo Error level: %EXEC_ERROR%
echo.

echo [Test 5] Python version via sys
echo ----------------------------------------
echo Running: python -c "import sys; print(sys.version)"
python -c "import sys; print(sys.version)"
echo.

echo [Test 6] pip module check
echo ----------------------------------------
echo Running: python -m pip --version
python -m pip --version
set PIP_ERROR=%ERRORLEVEL%
echo Error level: %PIP_ERROR%
echo.

echo [Test 7] Check if ensurepip is available
echo ----------------------------------------
echo Running: python -m ensurepip --version
python -m ensurepip --version
set ENSUREPIP_ERROR=%ERRORLEVEL%
echo Error level: %ENSUREPIP_ERROR%
echo.

echo ========================================
echo  Summary
echo ========================================
echo.
if %WHERE_ERROR% NEQ 0 (
    echo [CRITICAL] Python not found in PATH
    echo Solution: Reinstall Python with "Add to PATH" checked
) else if %VERSION_ERROR% NEQ 0 (
    echo [CRITICAL] Python command not working properly
    echo Solution: Reinstall Python
) else if %EXEC_ERROR% NEQ 0 (
    echo [CRITICAL] Python interpreter damaged
    echo Solution: Reinstall Python
) else if %PIP_ERROR% NEQ 0 (
    echo [WARNING] pip module not found
    echo Solution: Run Repair_pip.bat or reinstall Python
) else (
    echo [OK] Python and pip are working
)
echo.

echo ========================================
echo  Recommendations
echo ========================================
echo.
echo If you see "Python" without version number:
echo   - Your Python installation is corrupted
echo   - Download Python from: https://python.org
echo   - During installation, CHECK "Add Python to PATH"
echo   - Uninstall old Python first if needed
echo.
echo If pip is missing:
echo   - Run Repair_pip.bat
echo   - Or reinstall Python with pip included
echo.

pause
popd
endlocal
