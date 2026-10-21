@echo off
rem Builds the app on this PC (no installer, no auto-updates). Needs Python 3.12.
rem Uses its own clean Python environment in .venv so old packages on your PC can't break the build.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Creating build environment...
    py -3.12 -m venv .venv || goto :error
)
set PY=.venv\Scripts\python.exe
%PY% -m pip install --upgrade pip || goto :error
%PY% -m pip install --upgrade -r requirements.txt pyinstaller pyinstaller-hooks-contrib || goto :error
%PY% tools\fetch_model.py || goto :error
if exist dist\LaughClipper rmdir /s /q dist\LaughClipper
%PY% -m PyInstaller LaughClipper.spec --noconfirm --clean || goto :error
echo.
echo Done! Start it with: dist\LaughClipper\LaughClipper.exe
pause
exit /b 0
:error
echo Build failed.
pause
exit /b 1
