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

echo [1/4] Installing pyinstaller ...
%PY% -m pip install --quiet --upgrade pyinstaller

echo [2/4] Packing the mod index into the EXE ...
%PY% make_seed.py

echo [3/4] Building EXE (mods + index are packed inside) ...
%PY% -m PyInstaller --noconfirm --onefile --noconsole --name REPO-Mod-Installer --add-data "config.json;." --add-data "mods;mods" --add-data "build/seed/index.json.gz;seed" mod_installer.py

echo [4/4] Done.
echo(
if exist "dist\REPO-Mod-Installer.exe" goto ok
echo [ERROR] Build failed. Check the messages above.
pause
goto end

:ok
echo   Output:  dist\REPO-Mod-Installer.exe
echo(
echo   Copy that single EXE to your USB drive. The mod packages
echo   and the mod list index are packed inside it, so the list
echo   shows up on the first run even without internet.
echo(
echo   On first launch the EXE writes these next to itself:
echo     config.json   - settings (edit proxy here if needed)
echo     mods\         - offline mod packages, add your own zips
echo     data\         - index cache, icons, install records
echo(
pause
goto end

:nopython
echo [ERROR] No usable Python found. Build the EXE on a PC with Python 3.
pause

:end
endlocal
