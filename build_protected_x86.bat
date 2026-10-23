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
set "PY=py -3-32"
set "OUTNAME=EfimeriesPortable_x86"

echo ===============================================
echo Efimeries Portable v%APPVER% - Protected x86 Build
echo ===============================================
echo.

%PY% -c "import struct,sys;sys.exit(0 if struct.calcsize('P')*8==32 else 1)" >nul 2>nul
if errorlevel 1 goto no_python

echo [1/4] Installing/updating PyInstaller and PyArmor...
%PY% -m pip install --upgrade pyinstaller pyarmor
if errorlevel 1 goto error

echo.
echo [2/4] Removing old protected output...
if exist "dist\%OUTNAME%" rmdir /s /q "dist\%OUTNAME%" >nul 2>nul
if exist ".pyarmor" rmdir /s /q ".pyarmor" >nul 2>nul

echo.
echo [3/4] Obfuscating and packing...
%PY% -m pyarmor.cli gen --pack "%OUTNAME%.spec" -r app.py
if errorlevel 1 goto pyarmor_error

echo.
echo [4/4] Preparing portable folders...
if not exist "dist\%OUTNAME%\data\years" mkdir "dist\%OUTNAME%\data\years"
if not exist "dist\%OUTNAME%\exports" mkdir "dist\%OUTNAME%\exports"
if not exist "dist\%OUTNAME%\backups" mkdir "dist\%OUTNAME%\backups"
copy /Y "Odigos_Chrisis_Efimeries.pdf" "dist\%OUTNAME%\Odigos_Chrisis_Efimeries.pdf" >nul
copy /Y "app_icon.ico" "dist\%OUTNAME%\app_icon.ico" >nul
copy /Y "create_shortcut.bat" "dist\%OUTNAME%\create_shortcut.bat" >nul

echo.
echo PROTECTED BUILD COMPLETED.
echo Folder: "%CD%\dist\%OUTNAME%"
echo.
pause
exit /b 0

:pyarmor_error
echo.
echo ERROR: PyArmor could not create the protected build.
echo The current PyArmor free edition has limitations for large scripts.
echo If this is the reported reason, use a suitable PyArmor license or run the normal build script.
echo Your source code has NOT been deleted.
echo.
pause
exit /b 4

:no_python
echo ERROR: Required 32-bit Python not found using: %PY%
echo.
pause
exit /b 2

:error
echo ERROR: Protected build failed.
echo.
pause
exit /b 1
