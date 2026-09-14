@echo off
cd /d %~dp0
if not exist .venv (
  py -3 -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt
pyinstaller --noconfirm --clean --windowed --name ValorantLocalCoach --add-data "config.json;." main.py
if %errorlevel% neq 0 (
  echo Build failed.
  pause
  exit /b %errorlevel%
)
echo.
echo EXE created: dist\ValorantLocalCoach\ValorantLocalCoach.exe or dist\ValorantLocalCoach.exe
pause
