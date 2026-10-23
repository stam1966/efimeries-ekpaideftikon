@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "EXE="
for %%F in (EfimeriesPortable_x64.exe EfimeriesPortable_x86.exe) do (
  if exist "%%F" set "EXE=%%~fF"
)
if not defined EXE (
  echo ERROR: EfimeriesPortable_x64.exe or EfimeriesPortable_x86.exe was not found in this folder.
  pause
  exit /b 1
)
set "ICON=%~dp0app_icon.ico"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $desk=[Environment]::GetFolderPath('Desktop'); $lnk=$ws.CreateShortcut((Join-Path $desk 'Efimeries Ekpaideytikon.lnk')); $lnk.TargetPath='%EXE%'; $lnk.WorkingDirectory='%~dp0'; if(Test-Path '%ICON%'){$lnk.IconLocation='%ICON%,0'} else {$lnk.IconLocation='%EXE%,0'}; $lnk.Save()"
if errorlevel 1 (
  echo ERROR: Shortcut creation failed.
  pause
  exit /b 1
)
echo Shortcut created on Desktop.
pause
