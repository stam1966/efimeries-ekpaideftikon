@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PY="
set "BITS="

echo ===============================================
echo Efimeries Portable - Automatic Stable Build
echo ===============================================
echo.
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import struct;print(struct.calcsize('P')*8)" > "%TEMP%\efimeries_bits.txt" 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import struct;print(struct.calcsize('P')*8)" > "%TEMP%\efimeries_bits.txt" 2>nul
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY goto no_python
set /p BITS=<"%TEMP%\efimeries_bits.txt"
del /q "%TEMP%\efimeries_bits.txt" >nul 2>nul

if "%BITS%"=="64" (
    echo Detected 64-bit Python. Starting stable x64 build...
    call build_x64.bat
    exit /b %ERRORLEVEL%
)
if "%BITS%"=="32" (
    echo Detected 32-bit Python. Starting stable x86 build...
    call build_x86.bat
    exit /b %ERRORLEVEL%
)
echo ERROR: Could not detect Python architecture.
pause
exit /b 1

:no_python
echo ERROR: Python was not found.
echo Install Python and enable the Python Launcher or Add Python to PATH.
pause
exit /b 1
