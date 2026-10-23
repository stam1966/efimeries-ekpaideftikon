@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo ===============================================
echo Efimeries Portable - Stable x64 + x86 Builds
echo ===============================================
echo.
call build_x64.bat --no-pause
set "E64=%ERRORLEVEL%"
echo.
call build_x86.bat --no-pause
set "E86=%ERRORLEVEL%"
echo.
if "%E64%"=="0" echo x64: SUCCESS
if not "%E64%"=="0" echo x64: FAILED
if "%E86%"=="0" echo x86: SUCCESS
if not "%E86%"=="0" echo x86: FAILED
echo.
echo Each build is a folder under dist. Share the entire folder.
pause
if not "%E64%"=="0" exit /b %E64%
if not "%E86%"=="0" exit /b %E86%
exit /b 0
