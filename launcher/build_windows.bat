@echo off
:: build_windows.bat
:: Run this on a Windows machine to build MIDI Gen.exe
:: Requires Python 3.8+ installed and in PATH

setlocal
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"

echo ===== Building MIDI Gen.exe =====

:: Install PyInstaller if needed
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller -q
)

:: Build
pyinstaller ^
    --onefile ^
    --windowed ^
    --name "MIDI Gen" ^
    --icon "%HERE%\assets\icon.ico" ^
    --add-data "%HERE%\assets;assets" ^
    "%HERE%\launcher.py"

:: Result is in dist\MIDI Gen.exe
if exist "%HERE%\dist\MIDI Gen.exe" (
    echo.
    echo ============================================
    echo  Build successful!
    echo  Output: dist\MIDI Gen.exe
    echo ============================================
) else (
    echo.
    echo [ERROR] Build failed — check output above
)

pause