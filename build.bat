@echo off
chcp 65001 >nul
rem Сборка Konspekt.exe в папку dist
cd /d "%~dp0"
if not exist .venv python -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python -m pip install -q -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python tools\make_icon.py
if errorlevel 1 goto failed
.venv\Scripts\pyinstaller --noconfirm --clean Konspekt.spec
if errorlevel 1 goto failed
echo.
echo Готово: dist\Konspekt.exe
exit /b 0
:failed
echo Build failed. See the error above.
exit /b 1
