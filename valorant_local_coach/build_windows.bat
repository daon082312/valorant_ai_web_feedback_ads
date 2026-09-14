@echo off
cd /d %~dp0
if not exist .venv (
  py -3 -m venv .venv
)
call .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name ValorantLocalCoach app_v5_fixed.py
if exist dist\ValorantLocalCoach.exe (
  echo.
  echo Build complete: dist\ValorantLocalCoach.exe
) else (
  echo Build failed. Check the output above.
)
pause
