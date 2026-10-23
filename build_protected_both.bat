@echo off
setlocal EnableExtensions
cd /d "%~dp0"
rem --- Έκδοση: διαβάζεται από το app.py (APP_VERSION), ποτέ σταθερή ---
set "APPVER="
for /f "tokens=2 delims==" %%A in ('findstr /B /C:"APP_VERSION" app.py') do set "APPVER=%%A"
set "APPVER=%APPVER: =%"
set "APPVER=%APPVER:"=%"
if "%APPVER%"=="" (echo ERROR: APP_VERSION not found in app.py & pause & exit /b 1)
set "APPVER_U=%APPVER:.=_%"
echo ===============================================
echo Efimeries Portable v%APPVER% - Protected x64 + x86
 echo ===============================================
echo.
call build_protected_x64.bat
set "E64=%ERRORLEVEL%"
echo.
call build_protected_x86.bat
set "E86=%ERRORLEVEL%"
echo.
if "%E64%"=="0" echo x64: SUCCESS
if not "%E64%"=="0" echo x64: FAILED
if "%E86%"=="0" echo x86: SUCCESS
if not "%E86%"=="0" echo x86: FAILED
echo.
pause
if not "%E64%"=="0" exit /b %E64%
if not "%E86%"=="0" exit /b %E86%
exit /b 0
