@echo off
setlocal
title Build R.E.P.O. Mod Installer EXE
cd /d "%~dp0"

echo ============================================
echo   Build a single EXE (no Python needed)
echo   Run this once on a PC that HAS Python
echo ============================================
echo(

set "PY="

py -c "import tkinter" >nul 2>&1
if not errorlevel 1 set "PY=py"
if defined PY goto found

python -c "import tkinter" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto found

goto nopython

:found
echo Using Python: %PY%
echo(

echo [1/3] Installing pyinstaller ...
%PY% -m pip install --quiet --upgrade pyinstaller

echo [2/3] Building EXE (mods are packed inside) ...
%PY% -m PyInstaller --noconfirm --onefile --noconsole --name REPO-Mod-Installer --add-data "config.json;." --add-data "mods;mods" mod_installer.py

echo [3/3] Done.
echo(
if exist "dist\REPO-Mod-Installer.exe" goto ok
echo [ERROR] Build failed. Check the messages above.
pause
goto end

:ok
echo   Output:  dist\REPO-Mod-Installer.exe
echo(
echo   Copy that single EXE to your USB drive. The mod packages
echo   are packed inside it, so nothing else is required.
echo(
echo   Optional: also copy the "data" folder next to the EXE to
echo   keep the online mod index cache (bigger, but lets you
echo   browse the online list without downloading it again).
echo(
pause
goto end

:nopython
echo [ERROR] No usable Python found. Build the EXE on a PC with Python 3.
pause

:end
endlocal
