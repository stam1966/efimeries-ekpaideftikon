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
set "REL=release\EfimeriesPortable_v%APPVER_U%"

if exist "release" rmdir /s /q "release"
mkdir "%REL%"

if exist "dist\EfimeriesPortable_x64\EfimeriesPortable_x64.exe" (
  xcopy /E /I /Y "dist\EfimeriesPortable_x64" "%REL%\x64" >nul
) else (
  echo WARNING: x64 build not found.
)

if exist "dist\EfimeriesPortable_x86\EfimeriesPortable_x86.exe" (
  xcopy /E /I /Y "dist\EfimeriesPortable_x86" "%REL%\x86" >nul
) else (
  echo WARNING: x86 build not found.
)

mkdir "%REL%\Shared" >nul 2>nul
mkdir "%REL%\Shared\data\years" >nul 2>nul
mkdir "%REL%\Shared\backups" >nul 2>nul
mkdir "%REL%\Shared\exports" >nul 2>nul
copy /Y "Odigos_Chrisis_Efimeries.pdf" "%REL%\Odigos_Chrisis_Efimeries.pdf" >nul
copy /Y "DISTRIBUTION_README.txt" "%REL%\README.txt" >nul

powershell -NoProfile -Command "Compress-Archive -Path '%REL%\*' -DestinationPath 'release\EfimeriesPortable_v%APPVER_U%_Distribution.zip' -Force"

echo.
echo Distribution package created:
echo %CD%\release\EfimeriesPortable_v%APPVER_U%_Distribution.zip
echo.
pause
