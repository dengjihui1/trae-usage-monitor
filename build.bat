@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo [1/2] Installing / upgrading PyInstaller ...
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail

echo [2/2] Building single-file exe ...
python -m PyInstaller --noconfirm --clean --onefile --name trae-usage-monitor --add-data "web;web" trae_usage_monitor.py
if errorlevel 1 goto fail

echo.
echo Done. Output: dist\trae-usage-monitor.exe
echo Copy it to any Windows machine and double-click. No Python needed.
endlocal
exit /b 0

:fail
echo.
echo Build failed. See the errors above.
endlocal
exit /b 1
