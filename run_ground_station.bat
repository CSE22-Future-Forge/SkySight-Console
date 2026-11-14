@echo off
setlocal

rem SAR Manifest ground station (coordinator console + phone server).
rem Extra arguments are passed through, e.g.:  run_ground_station.bat --headless --pair
cd /d "%~dp0"

set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo.
    echo [ERROR] Virtual environment not found at "%VENV%".
    echo.
    echo Create it first with:
    echo     py -3 -m venv .venv
    echo     .venv\Scripts\python -m pip install -r ground_station\requirements.txt
    echo.
    pause
    exit /b 1
)

title SAR Manifest ground station
echo.
echo ==========================================
echo   SAR Manifest ground station
echo   Data: %LOCALAPPDATA%\SARManifest
echo ==========================================
echo.
echo Phones reach this laptop on TCP port 8000. If Windows asks, allow
echo access on PRIVATE networks only.
echo.

"%VENV%\Scripts\python.exe" -m ground_station.app %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo [ERROR] Ground station exited with code %CODE%. See %LOCALAPPDATA%\SARManifest\logs.
    pause
)

endlocal & exit /b %CODE%
