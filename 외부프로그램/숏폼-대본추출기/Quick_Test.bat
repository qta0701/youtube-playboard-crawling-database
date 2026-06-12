@echo off
echo ========================================
echo  Quick Python Test
echo ========================================
echo.

echo Test 1: Where is Python?
where python
echo.

echo Test 2: What version?
python --version 2>&1
echo.

echo Test 3: Can Python run?
python -c "print('Python is working!')"
echo.

echo Test 4: Can pip run?
python -m pip --version 2>&1
echo.

echo ========================================
pause
