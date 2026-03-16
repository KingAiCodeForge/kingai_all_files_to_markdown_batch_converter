@echo off
REM KingAI Markdown Converter v1.1 - Windows Launcher
REM Batch document conversion with graceful degradation

echo ============================================
echo   KingAI Markdown Converter v1.1
echo   Pre-flight validation + crash recovery
echo ============================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8+ from https://python.org
    pause
    exit /b 1
)

REM Check if markitdown is installed
python -c "import markitdown" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing dependencies...
    pip install -r "%~dp0requirements.txt"
)

REM Run the converter with passed arguments
python "%~dp0convert.py" %*

if %errorlevel% neq 0 (
    echo.
    echo Conversion completed with some errors.
    echo Use --resume to continue from checkpoint.
    pause
)
