@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] Installing / upgrading PyInstaller ...
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail

echo [2/3] Installing optional tray deps (pystray, pillow) ...
python -m pip install --upgrade pystray pillow
if errorlevel 1 echo   WARN: optional tray deps failed; tray will be disabled in this build

echo [3/3] Building single-file exe ...
rem --noconsole: the exe runs silently in the tray, no console window.
rem   CLI modes (--doctor / --json) attach their own console when one is missing.
rem --exclude-module numpy/psutil: pulled in transitively by PIL's PyInstaller hook, unused here,
rem   and numpy alone would add ~15 MB to the single-file exe.
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name trae-usage-monitor --icon "web\favicon.ico" --add-data "web;web" --exclude-module numpy --exclude-module psutil trae_usage_monitor.py
if errorlevel 1 goto fail

echo.
echo Done. Output: dist\trae-usage-monitor.exe
echo Copy it to any Windows machine and double-click. No Python needed.
echo It starts silently in the tray - no console window, no browser tab needed.
endlocal
exit /b 0

:fail
echo.
echo Build failed. See the errors above.
endlocal
exit /b 1
