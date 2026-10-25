@echo off
chcp 65001 >nul
rem Сборка Konspekt.exe в папку dist
cd /d "%~dp0"
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install -q -r requirements.txt
.venv\Scripts\python tools\make_icon.py
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed ^
  --name Konspekt --icon assets\icon.ico ^
  --add-data "assets\icon.ico;assets" ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore ^
  run.py
echo.
echo Готово: dist\Konspekt.exe
