@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

where "%PY%" >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.11+ and add it to PATH.
    pause
    exit /b 1
)

"%PY%" -c "import textual, requests, bs4, lxml" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
)

"%PY%" run.py %*
if errorlevel 1 pause
