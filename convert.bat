@echo off
REM KingAI Markdown Converter - Windows Launcher
REM Converts documents to Markdown with high-performance multiprocessing

echo ============================================
echo   KingAI Markdown Converter
echo   Optimized for i9-9900K (16 workers max)
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
    echo Installing markitdown library...
    pip install markitdown[all]
)

REM Run the converter with passed arguments
python "%~dp0convert.py" %*

if %errorlevel% neq 0 (
    echo.
    echo Conversion completed with some errors.
    pause
)
