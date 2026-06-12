@echo off
cd /d "%~dp0"
python -m pip install pyinstaller av pillow
python -m PyInstaller --noconfirm --clean --windowed --onefile --name AVI_Player avi_player.py
echo.
echo Done: dist\AVI_Player.exe
pause
