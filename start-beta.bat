@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe python -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python run.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Could not start Konspekt. See the error above.
pause
exit /b 1
