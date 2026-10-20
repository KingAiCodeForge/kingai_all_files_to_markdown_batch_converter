@echo off
REM Run from any working directory; arguments keep their caller-relative paths.
REM Install dependencies explicitly with: python -m pip install -r requirements.txt
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.10+ from https://python.org
    exit /b 1
)

REM Run the converter with passed arguments
python "%~dp0convert.py" %*
exit /b %errorlevel%
