@echo off
rem Builds the app on this PC (no installer, no auto-updates). Needs Python 3.12.
cd /d "%~dp0"
py -3.12 -m pip install -r requirements.txt pyinstaller || goto :error
py -3.12 tools\fetch_model.py || goto :error
py -3.12 -m PyInstaller LaughClipper.spec --noconfirm || goto :error
echo.
echo Done: dist\LaughClipper\LaughClipper.exe
pause
exit /b 0
:error
echo Build failed.
pause
exit /b 1
