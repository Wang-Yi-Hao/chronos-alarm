@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"

REM ---- One-click build script for Chronos alarm (pywebview + PyInstaller) ----
REM Dedicated virtual environment (isolated, does not pollute user Python)
set "VENV=%USERPROFILE%\.workbuddy\binaries\python\envs\chronos"
set "PY=%VENV%\Scripts\python.exe"
set "BASEPY=C:\Users\wyh17\.workbuddy\binaries\python\versions\3.13.12\python.exe"

if not exist "%PY%" (
    echo [build] Creating virtual environment...
    "%BASEPY%" -m venv "%VENV%"
)

echo [build] Installing dependencies (pywebview + pyinstaller)...
"%PY%" -m pip install --quiet --disable-pip-version-check pywebview pyinstaller
if errorlevel 1 (
    echo [build] Dependency installation FAILED.
    pause
    exit /b 1
)

echo [build] Cleaning previous build...
if exist dist\Chronos rmdir /s /q dist\Chronos
if exist build rmdir /s /q build

echo [build] Running PyInstaller...
"%PY%" -m PyInstaller build.spec --noconfirm --clean
if errorlevel 1 (
    echo [build] PyInstaller FAILED. Check the output above.
    pause
    exit /b 1
)

echo.
echo [build] Done!
echo [build] Executable: dist\Chronos\Chronos.exe
echo [build] Distribute the WHOLE dist\Chronos folder (keep _internal next to Chronos.exe).
echo [build] Requires WebView2 Runtime (preinstalled on Win10/11).
pause
