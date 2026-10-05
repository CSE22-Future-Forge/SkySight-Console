@echo off
setlocal

rem Always run from this script's folder, no matter where it is double-clicked from.
cd /d "%~dp0"

set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo.
    echo [ERROR] Virtual environment not found at "%VENV%".
    echo.
    echo Create it first with:
    echo     py -3 -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

rem Call the venv's Python directly instead of activating it: activate.bat
rem hardcodes the original install path, so it breaks when the folder is moved.
set "PYTHON=%VENV%\Scripts\python.exe"

title SkySight Console

echo.
echo ==========================================
echo   SkySight Console
echo   Python: %PYTHON%
echo ==========================================
echo.
echo Close the console window to stop the application.
echo.

"%PYTHON%" main.py
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo [ERROR] Application exited with code %CODE%.
    pause
)

endlocal & exit /b %CODE%
