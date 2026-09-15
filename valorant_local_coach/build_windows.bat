@echo off
cd /d %~dp0
if not exist .venv (
  py -3 -m venv .venv
)
call .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name ValorantVideoCoach --collect-all customtkinter app_v7_11.py
if exist dist\ValorantVideoCoach.exe (
  echo.
  echo Build complete: dist\ValorantVideoCoach.exe
) else (
  echo Build failed. Check the output above.
)
pause
