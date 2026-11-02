#!/usr/bin/env bash
# 打包单文件可执行程序（macOS / Linux）
# Windows 用户请用 build.bat
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

echo "[1/3] Installing / upgrading PyInstaller ..."
"$PY" -m pip install --upgrade pyinstaller

echo "[2/3] Installing optional tray deps (pystray, pillow) ..."
# macOS 上 pystray 会自动带上 pyobjc-framework-Quartz；Linux 上装的是 GTK 后端所需的包。
"$PY" -m pip install --upgrade pystray pillow || \
  echo "  WARN: optional tray deps failed; tray will be disabled in this build"

echo "[3/3] Building single-file binary ..."
# --icon: 同一个 web/favicon.ico 既作浏览器 favicon 也作可执行文件图标，避免两份图标漂移。
# 这里不加 --noconsole：macOS / Linux 上保留控制台输出更方便排查，
# 而且 windowed 行为在本项目尚未真机验证过，不贸然改。
# --exclude-module numpy/psutil: 被 PIL 的 PyInstaller hook 连带拉进来，这里用不到，
# 光 numpy 一个就会给单文件增加 ~15MB。
"$PY" -m PyInstaller --noconfirm --clean --onefile \
  --name trae-usage-monitor \
  --icon "web/favicon.ico" \
  --add-data "web:web" \
  --exclude-module numpy --exclude-module psutil \
  trae_usage_monitor.py

echo
echo "Done. Output: dist/trae-usage-monitor"
echo "Note: PyInstaller does NOT cross-compile. This binary only runs on the OS it was built on."
