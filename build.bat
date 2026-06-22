@echo off
setlocal
echo ============================================
echo  MSTS .s to OBJ Converter - build script
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on PATH.
    echo Install it from https://python.org and tick "Add python.exe to PATH",
    echo then run this script again.
    pause
    exit /b 1
)

echo [1/3] Installing dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 goto fail

python -m pip install pyinstaller
if errorlevel 1 goto fail

echo.
echo [2/3] Building MSTS_to_OBJ_Converter.exe ...
python -m PyInstaller --onefile --windowed --name "MSTS_to_OBJ_Converter" app.py
if errorlevel 1 goto fail

echo.
echo [3/3] Done!
echo Your exe is here:
echo   dist\MSTS_to_OBJ_Converter.exe
echo.
pause
exit /b 0

:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
