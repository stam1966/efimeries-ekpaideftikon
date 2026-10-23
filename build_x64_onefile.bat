@echo off
setlocal
cd /d "%~dp0"
py -3-64 -m pip install --upgrade pyinstaller
py -3-64 -m PyInstaller --noconfirm --clean --onefile --windowed --icon "app_icon.ico" --add-data "app_icon.ico;." --add-data "sv_ttk;sv_ttk" --hidden-import sv_ttk --name EfimeriesPortable_x64_onefile app.py
echo.
echo NOTE: One-file builds may be blocked by antivirus on some Windows PCs.
pause
