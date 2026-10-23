@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "NO_PAUSE=%~1"
set "PY="
set "OUTNAME=EfimeriesPortable_x64"

echo ===============================================
echo Efimeries Portable - Stable x64 Build (folder)
echo ===============================================
echo.

where py >nul 2>nul
if not errorlevel 1 (
    py -3-64 -c "import struct,sys;sys.exit(0 if struct.calcsize('P')*8==64 else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=py -3-64"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import struct,sys;sys.exit(0 if struct.calcsize('P')*8==64 else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY goto no_python

echo [1/3] Python architecture check...
%PY% -c "import struct;print('Python', struct.calcsize('P')*8, 'bit')"
if errorlevel 1 goto error

echo.
echo [2/3] Installing or updating PyInstaller...
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 goto error

echo.
echo [3/3] Building stable portable folder...
if exist "dist\%OUTNAME%" rmdir /s /q "dist\%OUTNAME%" >nul 2>nul
if exist "dist\%OUTNAME%" goto locked_output
%PY% -m PyInstaller --noconfirm --clean --onedir --windowed --icon "app_icon.ico" --add-data "app_icon.ico;." --add-data "sv_ttk;sv_ttk" --hidden-import sv_ttk --add-data "Odigos_Chrisis_Efimeries.pdf;." --name %OUTNAME% app.py
if errorlevel 1 goto error

if not exist "dist\%OUTNAME%\data" mkdir "dist\%OUTNAME%\data"
if not exist "dist\%OUTNAME%\data\years" mkdir "dist\%OUTNAME%\data\years"
if not exist "dist\%OUTNAME%\exports" mkdir "dist\%OUTNAME%\exports"
if not exist "dist\%OUTNAME%\backups" mkdir "dist\%OUTNAME%\backups"
copy /Y "app_icon.ico" "dist\%OUTNAME%\app_icon.ico" >nul
copy /Y "Odigos_Chrisis_Efimeries.pdf" "dist\%OUTNAME%\Odigos_Chrisis_Efimeries.pdf" >nul
copy /Y "create_shortcut.bat" "dist\%OUTNAME%\create_shortcut.bat" >nul

echo.
echo BUILD COMPLETED SUCCESSFULLY.
echo RUN: "%CD%\dist\%OUTNAME%\%OUTNAME%.exe"
echo IMPORTANT: Copy/share the WHOLE "%OUTNAME%" folder, not only the EXE.
echo.
if /I not "%NO_PAUSE%"=="--no-pause" pause
exit /b 0

:locked_output
echo.
echo ERROR: The existing output folder is in use or cannot be removed.
echo Close the application and any Explorer window using files in it, then retry.
echo.
if /I not "%NO_PAUSE%"=="--no-pause" pause
exit /b 3

:no_python
echo ERROR: A 64-bit Python installation was not found.
echo Install 64-bit Python and enable the Python Launcher or Add Python to PATH.
echo.
if /I not "%NO_PAUSE%"=="--no-pause" pause
exit /b 2

:error
echo.
echo ERROR: The x64 build failed.
echo Review the error messages above.
echo.
if /I not "%NO_PAUSE%"=="--no-pause" pause
exit /b 1
