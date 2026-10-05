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

title SkySight Console

rem Activate the virtual environment for this session.
call "%VENV%\Scripts\activate.bat"

echo.
echo ==========================================
echo   SkySight Console
echo   Python: %VENV%\Scripts\python.exe
echo ==========================================
echo.
echo Close the console window to stop the application.
echo.

python main.py
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo [ERROR] Application exited with code %CODE%.
    pause
)

endlocal & exit /b %CODE%
