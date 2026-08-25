@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo   MQL ONE - build MQL_ONE.exe
echo ============================================================
where python >nul 2>&1 || ( echo Install Python from python.org ^(tick "Add to PATH"^). & start https://www.python.org/downloads/ & pause & exit /b 1 )
echo Closing any running instance...
taskkill /f /im MQL_ONE.exe >nul 2>&1
echo Installing PyInstaller ^(one time^)...
python -m pip install --quiet --disable-pip-version-check pyinstaller || ( echo pip failed & pause & exit /b 1 )
echo Installing optional drag-drop support ^(safe to skip^)...
python -m pip install --quiet --disable-pip-version-check tkinterdnd2 >nul 2>&1
echo Building MQL_ONE.exe ...
python -m PyInstaller --onefile --clean --noconsole --name MQL_ONE ^
  --add-data "modules;modules" --collect-all tkinterdnd2 ^
  --hidden-import sqlite3 --hidden-import difflib --hidden-import subprocess ^
  --hidden-import shutil --hidden-import zipfile --hidden-import platform ^
  --hidden-import re --hidden-import hashlib --hidden-import concurrent.futures ^
  "%~dp0MQL_ONE_app.py"
if not exist "%~dp0dist\MQL_ONE.exe" ( echo BUILD FAILED - scroll up. & pause & exit /b 1 )
copy /y "%~dp0dist\MQL_ONE.exe" "%~dp0MQL_ONE.exe" >nul
echo.
echo Done. MQL_ONE.exe is in THIS folder. Keep the modules\ folder beside it.
pause
