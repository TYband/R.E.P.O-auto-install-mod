@echo off
setlocal
title R.E.P.O. Mod Installer
cd /d "%~dp0"

echo ============================================
echo    R.E.P.O. One-Click Mod Installer
echo ============================================
echo(

REM ---- find a Python that has tkinter (the GUI needs it) ----
set "PY="

py -c "import tkinter" >nul 2>&1
if not errorlevel 1 set "PY=py"
if defined PY goto found

python -c "import tkinter" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto found

python3 -c "import tkinter" >nul 2>&1
if not errorlevel 1 set "PY=python3"
if defined PY goto found

goto nopython

:found
echo Using Python: %PY%
echo(

REM ---- make sure requests is available ----
%PY% -c "import requests" >nul 2>&1
if not errorlevel 1 goto run

echo Installing required package: requests ...
%PY% -m pip install --quiet requests
%PY% -c "import requests" >nul 2>&1
if not errorlevel 1 goto run

echo Retrying with a China mirror ...
%PY% -m pip install --quiet requests -i https://pypi.tuna.tsinghua.edu.cn/simple

:run
%PY% mod_installer.py
if errorlevel 1 pause
goto end

:nopython
echo [ERROR] No usable Python found on this PC.
echo(
echo   Download Python 3 from:
echo     https://www.python.org/downloads/
echo(
echo   During setup, check BOTH of these:
echo     [x] Add python.exe to PATH
echo     [x] tcl/tk and IDLE
echo(
echo   Then run this file again.
echo(
echo   ALTERNATIVE: build the EXE once on your own PC with
echo   "build-exe.bat", then just run the EXE at the cafe.
echo(
pause

:end
endlocal
