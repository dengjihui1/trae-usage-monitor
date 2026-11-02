#!/usr/bin/env bash
# macOS 双击启动：Finder 里双击本文件即可运行看板。
# 首次使用若提示权限不足，请在终端执行：chmod +x start.command
cd "$(dirname "$0")" || exit 1
PY="${PYTHON:-python3}"
exec "$PY" trae_usage_monitor.py "$@"
