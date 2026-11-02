#!/usr/bin/env python3
"""Trae 用量与模型归因监控。

解析 Trae CN 客户端本地 renderer 日志，把「每一轮对话实际落到的模型」还原出来，
按模型 / 按天 / 按会话做聚合统计，并提供本地看板。

零依赖，只用 Python 3.10+ 标准库。

用法：
    python trae_usage_monitor.py                 # 启动看板 http://127.0.0.1:8788
    python trae_usage_monitor.py --port 9000     # 换端口
    python trae_usage_monitor.py --json          # 只打印解析结果到 stdout
    python trae_usage_monitor.py --out data.json # 导出快照
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _base_dir() -> Path:
    """打包成单文件 exe 后，静态资源在 PyInstaller 的临时解包目录里。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


APP_DIR = _base_dir()
WEB_DIR = APP_DIR / "web"

LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}(?:[+-]\d{2}:\d{2}|Z))"
    r"\s+\[[^\]]*\]\s+(?P<body>.*)$"
)

EV_SNAPSHOT = "created chat request selection snapshot "
EV_AGENTINFO = "created empty user message selection snapshot "
EV_MODE = "model auto selection resolved "
EV_REPORT = "reportFrontResponse, status: "


# Trae 的配置目录名随发行版略有差异，按优先级尝试，找不到再扫 Trae* 兜底。
_APP_DIRS = ("Trae CN", "Trae", "Trae Solo")


def _stderr(msg: str) -> None:
    """安全写 stderr。

    PyInstaller 以 --noconsole 打包时 sys.stderr 可能是 None，直接写会抛异常；
    而 BaseHTTPRequestHandler.log_message 在发响应之前就会被调用，
    一旦这里抛异常整个请求都会废掉，看板直接打不开。
    """
    try:
        if sys.stderr is not None:
            sys.stderr.write(msg)
    except Exception:  # noqa: BLE001
        pass


def _ensure_console() -> bool:
    """确保 stdout/stderr 可用；windowed 打包下没有控制台就现补一个。

    两处需要它：命令行模式（--doctor / --json / --out）否则会静默无输出；
    以及「托盘装不上」的降级路径，否则会变成一个既没托盘也没控制台、
    只能去任务管理器强杀的进程。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return True
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # 正常情况没有控制台，这里现补一个；已经有控制台的少见情况会返回 0，
        # 那就直接复用，靠下面的 CONOUT$ 能否打开来判定成败。
        kernel32.AllocConsole()
        # 新分配的控制台默认用系统 ANSI 代码页（简体中文是 936），而下面我们按
        # UTF-8 写，不切成 65001 的话中文会满屏乱码。
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        return True
    except Exception:  # noqa: BLE001
        return False


def _message_box(title: str, text: str, error: bool = False) -> None:
    """弹一个原生对话框 —— windowed 打包下没有控制台可以打印。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:  # noqa: BLE001
        pass


def _report_fatal(message: str, error: bool = False) -> None:
    """启动期致命错误的反馈 —— 进程马上就要退，所以提示必须留得住。

    windowed 打包下没有控制台：这时候现补一个也没用，控制台会跟着进程一起消失，
    用户只会看到黑框一闪，所以必须弹窗。本来就带控制台的情况（从命令行跑）直接打印。
    """
    if sys.stdout is not None and sys.stderr is not None:
        _stderr(message + "\n")
        return
    _message_box("Trae 用量监控", message, error=error)


def _port_in_use(port: int) -> bool:
    """端口上是否已经有东西在监听。

    必须自己探一下，不能指望 bind 报错：http.server 默认开着 SO_REUSEADDR，
    而 Windows 上的这个选项会放行第二个进程绑到同一端口并**抢走**流量 ——
    表现为两个实例都活着、只有后启动的那个在服务，用户会莫名其妙看到两个托盘图标。
    探测一把就能在抢端口之前干净地拒掉。
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _config_base() -> Path:
    """各平台存放应用数据的根目录（VS Code 系惯例）。"""
    home = Path.home()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return Path(base) if base else home / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else home / ".config"


def candidate_log_roots() -> list[Path]:
    """所有可能的日志根目录，按优先级排列。"""
    base = _config_base()
    out: list[Path] = []
    for name in _APP_DIRS:
        out.append(base / name / "logs")
    try:
        for d in sorted(base.glob("Trae*")):
            p = d / "logs"
            if p.is_dir() and p not in out:
                out.append(p)
    except OSError:
        pass
    return out


def default_log_root() -> Path:
    cands = candidate_log_roots()
    for p in cands:
        if p.is_dir():
            return p
    return cands[0]


def default_downloads_dir() -> Path:
    """系统下载目录。Windows 用 %USERPROFILE%\\Downloads，macOS/Linux 用 ~/Downloads。"""
    home = Path.home()
    if sys.platform == "win32":
        base = os.environ.get("USERPROFILE")
        return (Path(base) if base else home) / "Downloads"
    return home / "Downloads"


def _json_tail(text: str) -> dict | None:
    start = text.find("{")
    if start < 0:
        return None
    end = text.rfind("}")
    if end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _parse_ts(ts: str) -> float:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return 0.0


def normalize_model(name: str | None) -> str:
    """把 `custom_responses_compatible//gpt-6.1-sol` 归一成 `gpt-6.1-sol`。"""
    if not name:
        return ""
    name = name.strip().lower()
    if "//" in name:
        name = name.split("//")[-1]
    return name


def short_model(name: str) -> str:
    """展示用：去掉 provider 前缀，保留原始大小写。"""
    if not name:
        return ""
    return name.split("//")[-1]


@dataclass
class RawEvent:
    kind: str
    ts: float
    ts_iso: str
    data: dict


@dataclass
class FileCache:
    size: int
    mtime: float
    events: list[RawEvent] = field(default_factory=list)


class LogParser:
    def __init__(self, log_root: Path):
        self.log_root = log_root
        self._cache: dict[str, FileCache] = {}

    # ---------- 文件发现 ----------

    def discover_files(self) -> list[Path]:
        """按 时间先后 排序：先按运行目录名，再按 window 序号，再按轮转序号。"""
        if not self.log_root.is_dir():
            return []
        out: list[tuple[tuple, Path]] = []
        for run_dir in self.log_root.iterdir():
            if not run_dir.is_dir():
                continue
            for win_dir in sorted(run_dir.glob("window*")):
                for f in win_dir.glob("renderer*.log"):
                    out.append(((_run_key(run_dir.name), _win_key(win_dir.name), _rot_key(f.name)), f))
        out.sort(key=lambda x: x[0])
        return [f for _, f in out]

    # ---------- 解析 ----------

    def parse_file(self, path: Path) -> list[RawEvent]:
        key = str(path)
        try:
            st = path.stat()
        except OSError:
            return []
        cached = self._cache.get(key)
        if cached and cached.size == st.st_size and cached.mtime == st.st_mtime:
            return cached.events

        events: list[RawEvent] = []
        with open(path, "rb") as fh:
            for raw in fh:
                line = raw.decode("utf-8", "ignore")
                if (
                    EV_SNAPSHOT not in line
                    and EV_AGENTINFO not in line
                    and EV_MODE not in line
                    and EV_REPORT not in line
                ):
                    continue
                m = LINE_RE.match(line)
                if not m:
                    continue
                body = m.group("body")
                ts_iso = m.group("ts")
                if EV_AGENTINFO in body:
                    data = _json_tail(body[body.index(EV_AGENTINFO) + len(EV_AGENTINFO) :])
                    if data:
                        events.append(RawEvent("agentinfo", _parse_ts(ts_iso), ts_iso, data))
                elif EV_SNAPSHOT in body:
                    data = _json_tail(body[body.index(EV_SNAPSHOT) + len(EV_SNAPSHOT) :])
                    if data:
                        events.append(RawEvent("snapshot", _parse_ts(ts_iso), ts_iso, data))
                elif EV_MODE in body:
                    data = _json_tail(body[body.index(EV_MODE) + len(EV_MODE) :])
                    if data:
                        events.append(RawEvent("mode", _parse_ts(ts_iso), ts_iso, data))
                elif EV_REPORT in body:
                    tail = body[body.index(EV_REPORT) + len(EV_REPORT) :]
                    status = ""
                    if "," not in tail:
                        continue
                    status = tail.split(",", 1)[0].strip()
                    data = _json_tail(tail)
                    if data:
                        data["_status"] = status
                        events.append(RawEvent("report", _parse_ts(ts_iso), ts_iso, data))

        self._cache[key] = FileCache(size=st.st_size, mtime=st.st_mtime, events=events)
        return events

    def load_all(self) -> tuple[list[RawEvent], list[Path]]:
        files = self.discover_files()
        all_events: list[RawEvent] = []
        for f in files:
            all_events.extend(self.parse_file(f))
        all_events.sort(key=lambda e: e.ts)
        # 移除已消失文件的缓存
        alive = {str(f) for f in files}
        for k in list(self._cache):
            if k not in alive:
                del self._cache[k]
        return all_events, files


def _run_key(name: str) -> str:
    return name


def _win_key(name: str) -> int:
    digits = re.findall(r"\d+", name)
    return int(digits[0]) if digits else 0


def _rot_key(name: str) -> int:
    m = re.match(r"renderer\.(\d+)\.log", name)
    if m:
        return -int(m.group(1))
    return 0


# ---------- 归因 ----------


def build_turns(events: list[RawEvent]) -> dict:
    snapshots: list[dict] = []
    modes: dict[tuple[str, str], list[dict]] = {}
    agent_names: dict[tuple[str, str], str] = {}
    reports: list[RawEvent] = []

    for ev in events:
        if ev.kind == "agentinfo":
            key = (ev.data.get("sessionId", ""), ev.data.get("agentId", ""))
            name = ev.data.get("agentName") or ""
            if name:
                agent_names[key] = name
        elif ev.kind == "snapshot":
            snapshots.append(
                {
                    "ts": ev.ts,
                    "ts_iso": ev.ts_iso,
                    "sessionId": ev.data.get("sessionId", ""),
                    "messageId": ev.data.get("messageId", ""),
                    "agentId": ev.data.get("agentId", ""),
                    "agentType": ev.data.get("agentType", ""),
                    "agentName": ev.data.get("agentName", ""),
                    "modelName": ev.data.get("modelName", ""),
                    "provider": ev.data.get("customModelProvider", ""),
                    "configSource": ev.data.get("customModelConfigSource", ""),
                }
            )
        elif ev.kind == "mode":
            key = (ev.data.get("sessionId", ""), ev.data.get("agentId", ""))
            modes.setdefault(key, []).append(
                {
                    "ts": ev.ts,
                    "strategy": ev.data.get("strategy", ""),
                    "source": ev.data.get("source", ""),
                    "agentType": ev.data.get("agentType", ""),
                }
            )
        else:
            reports.append(ev)

    # 时间序列已排序，各 session 的 mode 事件天然有序
    def mode_for(snap: dict) -> dict | None:
        key = (snap["sessionId"], snap["agentId"])
        cands = modes.get(key)
        if not cands:
            # 退化：同 session 任意 agent
            for k, v in modes.items():
                if k[0] == snap["sessionId"]:
                    cands = v
                    break
        if not cands:
            return None
        # mode 事件通常紧邻 snapshot（可能在其之后几十毫秒），取时间最近的一条
        return min(cands, key=lambda m: abs(m["ts"] - snap["ts"]))

    pending = list(range(len(snapshots)))
    for snap in snapshots:
        m = mode_for(snap)
        snap["mode"] = (m["strategy"] if m else "unknown") or "unknown"
        snap["modeSource"] = m["source"] if m else ""
        snap["matched_report"] = None

    unmatched: list[dict] = []
    for rep in reports:
        rd = rep.data
        want = normalize_model(rd.get("model") or rd.get("originModel"))
        pick = None
        for idx in pending:
            if normalize_model(snapshots[idx]["modelName"]) == want:
                pick = idx
                break
        if pick is None:
            unmatched.append(
                {
                    "time": rep.ts_iso,
                    "ts": rep.ts,
                    "model": short_model(rd.get("model")),
                    "status": rd.get("_status", ""),
                    "costTime": rd.get("costTime"),
                    "errCode": rd.get("errCode", ""),
                    "traceId": rd.get("traceId", ""),
                }
            )
            continue
        pending.remove(pick)
        snapshots[pick]["matched_report"] = {
            "status": rd.get("_status", ""),
            "costTime": rd.get("costTime"),
            "errCode": rd.get("errCode", ""),
            "traceId": rd.get("traceId", ""),
            "tokenOutputInterval": rd.get("tokenOutputInterval"),
            "modelProviderName": rd.get("modelProviderName", ""),
            "errorMsg": rd.get("errorMsg", ""),
        }

    turns = []
    for snap in snapshots:
        rep = snap.get("matched_report") or {}
        turns.append(
            {
                "ts": snap["ts"],
                "time": snap["ts_iso"],
                "sessionId": snap["sessionId"],
                "messageId": snap["messageId"],
                "agentId": snap["agentId"],
                "agentType": snap["agentType"],
                "agentName": snap["agentName"] or agent_names.get((snap["sessionId"], snap["agentId"]), ""),
                "model": short_model(snap["modelName"]),
                "rawModel": snap["modelName"],
                "provider": snap["provider"],
                "mode": snap["mode"],
                "modeSource": snap["modeSource"],
                "status": rep.get("status", "Unknown"),
                "costTime": rep.get("costTime"),
                "errCode": rep.get("errCode", ""),
                "traceId": rep.get("traceId", ""),
                "errorMsg": rep.get("errorMsg", ""),
                "tokenOutputInterval": rep.get("tokenOutputInterval"),
                "matched": bool(snap.get("matched_report")),
            }
        )

    turns.sort(key=lambda t: t["ts"], reverse=True)

    sessions: dict[str, dict] = {}
    for t in sorted(turns, key=lambda x: x["ts"]):
        s = sessions.setdefault(
            t["sessionId"],
            {
                "sessionId": t["sessionId"],
                "agentName": "",
                "agentType": "",
                "firstTs": t["ts"],
                "lastTs": t["ts"],
                "turns": 0,
                "autoTurns": 0,
            },
        )
        if t["agentName"]:
            s["agentName"] = t["agentName"]
        if t["agentType"]:
            s["agentType"] = t["agentType"]
        s["lastTs"] = max(s["lastTs"], t["ts"])
        s["turns"] += 1
        if t["mode"] == "auto":
            s["autoTurns"] += 1

    return {"turns": turns, "unmatchedReports": unmatched, "sessions": list(sessions.values())}


# ---------- 官方控制台入口识别 ----------
# 本工具只统计本地日志里看得见的用量（轮次 / 耗时 / 状态 / 模型路由），额度与余额只有官方有。
# 但官方入口随版本完全不同：国内个人版、国际版、企业版是三个不同域名，
# 写死任何一个都会让别的用户点开 404。所以这里从日志里把「这台机器上的客户端
# 实际在用的那个控制台域名」认出来，而不是硬编码。

_CONSOLE_HOST_RE = re.compile(r'"consoleHost"\s*:\s*"(https?://[^"]+)"')
_CONSOLE_URL_RE = re.compile(r"https://console[\w.\-]*trae[\w.\-]*")
_OFFICIAL_SITE = {"cn": "https://www.trae.cn", "intl": "https://www.trae.ai"}

_CONSOLE_CACHE: dict[str, dict] = {}
_CONSOLE_LOCK = threading.Lock()


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _read_head(path: Path, limit: int = 3 * 1024 * 1024) -> str:
    """只读开头一段。控制台地址这类配置行出现在日志靠前的位置，读全量没必要。"""
    try:
        with path.open("rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def detect_console(log_root: Path) -> dict:
    """认出用户用的是哪个官方控制台，供「查看官方用量」入口跳转。

    认不出来就退回区域对应的官网首页 —— 宁可不直达，也不给一个会失效的深链。
    """
    key = str(log_root)
    with _CONSOLE_LOCK:
        hit = _CONSOLE_CACHE.get(key)
    if hit is not None:
        return hit

    try:
        files = sorted(LogParser(log_root).discover_files(), key=_safe_mtime, reverse=True)
    except Exception:  # noqa: BLE001
        files = []

    host = ""
    for path in files[:3]:
        text = _read_head(path)
        if not text:
            continue
        hosts = _CONSOLE_HOST_RE.findall(text)
        if hosts:
            # 同一份配置会被重复打印，取出现最多的那个更稳
            host = max(set(hosts), key=hosts.count).rstrip("/")
            break
        found = _CONSOLE_URL_RE.findall(text)
        if found:
            host = max(set(found), key=found.count).rstrip("/")
            break

    if host:
        out = {
            "consoleUrl": host,
            "edition": "enterprise" if "enterprise" in host else "personal",
            "region": "intl" if host.endswith("trae.ai") else "cn",
            "source": "log",
        }
    else:
        # 认不出来：按日志里出现的 CDN / API 域名猜区域，再退回对应官网首页
        blob = "".join(_read_head(p, 512 * 1024) for p in files[:3])
        intl = blob.count("trae.ai")
        cn = blob.count("trae.com.cn") + blob.count("trae.cn")
        region = "intl" if intl > cn else "cn"
        out = {
            "consoleUrl": _OFFICIAL_SITE[region],
            "edition": "unknown",
            "region": region,
            "source": "guess",
        }

    with _CONSOLE_LOCK:
        _CONSOLE_CACHE[key] = out
    return out


def snapshot_payload(log_root: Path, parser: LogParser | None = None) -> dict:
    parser = parser or LogParser(log_root)
    events, files = parser.load_all()
    parsed = build_turns(events)
    files_meta = []
    for f in files:
        try:
            st = f.stat()
            files_meta.append(
                {"path": str(f), "size": st.st_size, "mtime": st.st_mtime}
            )
        except OSError:
            pass
    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "logRoot": str(log_root),
        "logRootExists": log_root.is_dir(),
        "files": files_meta,
        "official": detect_console(log_root),
        "counts": {
            "events": len(events),
            "turns": len(parsed["turns"]),
            "matched": sum(1 for t in parsed["turns"] if t["matched"]),
            "unmatchedReports": len(parsed["unmatchedReports"]),
        },
        "turns": parsed["turns"],
        "sessions": parsed["sessions"],
        "unmatchedReports": parsed["unmatchedReports"],
    }


# ---------- 用户设置（提醒规则） ----------
# 说明：本地日志拿不到云端「额度余额」和 token 数值，所以提醒一律基于「本地用量口径」：
# 轮次、耗时、响应状态。用轮次去反推额度会严重误导（不同模型单价差几十倍），不做。

DEFAULT_SETTINGS: dict = {
    "alerts": {
        "enabled": True,
        "todayTurns": {"enabled": True, "threshold": 300},
        "sessionTurns": {"enabled": True, "threshold": 80},
        "errorRate": {"enabled": True, "threshold": 20, "minSamples": 5},
        "avgCost": {"enabled": False, "threshold": 60000, "minSamples": 5},
        "autoSwitch": {"enabled": False, "model": ""},
    },
    "pollSeconds": 2,
    "dashboardSeconds": 30,
    "notify": {"browser": False, "tray": True},
}


def settings_path() -> Path:
    """放在用户主目录，而不是程序目录：打包成 exe 后程序目录是临时解包目录，写不住。"""
    return Path.home() / ".trae-usage-monitor" / "settings.json"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    raw: dict = {}
    try:
        p = settings_path()
        if p.is_file():
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
    except (OSError, json.JSONDecodeError):
        raw = {}
    return _deep_merge(DEFAULT_SETTINGS, raw)


def save_settings(patch: dict) -> dict:
    merged = _deep_merge(load_settings(), patch if isinstance(patch, dict) else {})
    p = settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return merged


def _local_day_start() -> float:
    now = datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _as_int(value, fallback: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _as_float(value, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def evaluate_alerts(payload: dict, settings: dict) -> list[dict]:
    """按当前数据求值「此刻正在触发」的提醒。

    这是状态型而不是事件型：同一条件只要还成立就一直返回，去重与弹窗交给前端/托盘做。
    好处是刷新、重开页面、多端同时看都不会漏也不会重复堆叠。
    """
    cfg_all = settings.get("alerts") or {}
    if not cfg_all.get("enabled", True):
        return []

    turns = payload.get("turns") or []
    day_start = _local_day_start()
    today = [t for t in turns if _as_float(t.get("ts")) >= day_start]
    out: list[dict] = []

    # 1) 今日轮次
    cfg = cfg_all.get("todayTurns") or {}
    if cfg.get("enabled"):
        th = _as_int(cfg.get("threshold"))
        if th > 0 and len(today) > th:
            out.append({
                "code": "todayTurns",
                "level": "warn",
                "title": "今日轮次偏多",
                "detail": f"今日已 {len(today)} 轮，超过阈值 {th} 轮",
                "value": len(today),
                "threshold": th,
            })

    # 2) 单会话轮次（只看今天）
    cfg = cfg_all.get("sessionTurns") or {}
    if cfg.get("enabled"):
        th = _as_int(cfg.get("threshold"))
        if th > 0:
            per: dict[str, int] = {}
            for t in today:
                sid = t.get("sessionId") or ""
                per[sid] = per.get(sid, 0) + 1
            ranked = sorted(per.items(), key=lambda kv: -kv[1])
            if ranked and ranked[0][1] > th:
                sid, n = ranked[0]
                out.append({
                    "code": "sessionTurns",
                    "level": "warn",
                    "title": "单个会话轮次偏多",
                    "detail": f"会话 {sid[:8]} 今日已 {n} 轮，超过阈值 {th} 轮",
                    "value": n,
                    "threshold": th,
                    "sessionId": sid,
                })

    # 3) 未归属/失败率（只看有明确状态的轮次）
    cfg = cfg_all.get("errorRate") or {}
    if cfg.get("enabled"):
        th = _as_float(cfg.get("threshold"))
        need = _as_int(cfg.get("minSamples"), 1)
        known = [t for t in turns if t.get("status") and t.get("status") != "Unknown"]
        if th > 0 and len(known) >= need:
            bad = sum(1 for t in known if t.get("status") != "Success")
            rate = bad * 100.0 / len(known)
            if rate >= th:
                out.append({
                    "code": "errorRate",
                    "level": "bad",
                    "title": "响应失败率偏高",
                    "detail": f"统计 {len(known)} 轮里有 {bad} 轮失败，失败率 {rate:.0f}%（阈值 {th:.0f}%）",
                    "value": round(rate, 1),
                    "threshold": th,
                })

    # 4) 平均耗时
    cfg = cfg_all.get("avgCost") or {}
    if cfg.get("enabled"):
        th = _as_float(cfg.get("threshold"))
        need = _as_int(cfg.get("minSamples"), 1)
        durs = [_as_float(t.get("costTime")) for t in turns]
        durs = [d for d in durs if d > 0]
        if th > 0 and len(durs) >= need:
            avg = sum(durs) / len(durs)
            if avg >= th:
                out.append({
                    "code": "avgCost",
                    "level": "warn",
                    "title": "平均耗时偏高",
                    "detail": f"平均 {avg / 1000:.1f}s（阈值 {th / 1000:.1f}s，样本 {len(durs)} 轮）",
                    "value": round(avg),
                    "threshold": th,
                })

    # 5) Auto 落到关注模型
    cfg = cfg_all.get("autoSwitch") or {}
    if cfg.get("enabled"):
        want = normalize_model(cfg.get("model") or "")
        if want:
            ordered = sorted(turns, key=lambda t: _as_float(t.get("ts")))
            hits = [t for t in ordered if t.get("mode") == "auto" and normalize_model(t.get("model")) == want]
            if hits:
                last = hits[-1]
                out.append({
                    "code": "autoSwitch",
                    "level": "info",
                    "title": "Auto 落到关注模型",
                    "detail": f"最近一次 Auto 命中 {last.get('model')}（{str(last.get('time', ''))[11:19]}）",
                    "value": last.get("model"),
                })

    return out


def _files_stamp(files: list[dict]) -> str:
    """日志文件的指纹：没变就说明没有新增对话，前端可以跳过重绘。"""
    if not files:
        return "0:0:0"
    total = sum(_as_int(f.get("size")) for f in files)
    latest = max(_as_float(f.get("mtime")) for f in files)
    return f"{len(files)}:{total}:{int(latest)}"


def live_payload(payload: dict, settings: dict) -> dict:
    """给「实时面板 + 提醒」用的精简载荷：体积小、字段少、可以秒级轮询。"""
    turns = payload.get("turns") or []
    day_start = _local_day_start()
    today = [t for t in turns if _as_float(t.get("ts")) >= day_start]
    ordered = sorted(turns, key=lambda t: _as_float(t.get("ts")))

    known = [t for t in today if t.get("status") and t.get("status") != "Unknown"]
    ok = sum(1 for t in known if t.get("status") == "Success")
    auto = sum(1 for t in today if t.get("mode") == "auto")
    durs = [_as_float(t.get("costTime")) for t in today]
    durs = [d for d in durs if d > 0]

    last = ordered[-1] if ordered else None
    cur_sid = ""
    cur_turns = 0
    if last and time.time() - _as_float(last.get("ts")) <= 600:
        cur_sid = last.get("sessionId") or ""
        cur_turns = sum(1 for t in today if t.get("sessionId") == cur_sid)

    def slim(t: dict) -> dict:
        return {
            "ts": t.get("ts"),
            "time": t.get("time"),
            "sessionId": t.get("sessionId"),
            "model": t.get("model"),
            "mode": t.get("mode"),
            "status": t.get("status"),
            "costTime": t.get("costTime"),
        }

    alerts = evaluate_alerts(payload, settings)
    return {
        "generatedAt": payload.get("generatedAt"),
        "stamp": _files_stamp(payload.get("files") or []),
        "logRootExists": payload.get("logRootExists", False),
        "total": len(turns),
        "today": {
            "turns": len(today),
            "autoTurns": auto,
            "known": len(known),
            "success": ok,
            "successRate": round(ok * 100.0 / len(known), 1) if known else None,
            "avgCostTime": round(sum(durs) / len(durs)) if durs else None,
        },
        "current": {"sessionId": cur_sid, "turns": cur_turns},
        "last": slim(last) if last else None,
        "recent": [slim(t) for t in ordered[-24:]],
        "alerts": alerts,
        "settings": {
            "pollSeconds": _as_float(settings.get("pollSeconds"), 2) or 2,
            "dashboardSeconds": _as_float(settings.get("dashboardSeconds"), 30) or 30,
            "alertsEnabled": bool((settings.get("alerts") or {}).get("enabled", True)),
            "browserNotify": bool((settings.get("notify") or {}).get("browser", False)),
            "trayNotify": bool((settings.get("notify") or {}).get("tray", True)),
        },
    }


# ---------- 控制台用量明细 CSV 识别 ----------
# 本地拿不到精确 token，只能从控制台「个人用量 → 用量明细」导出的 CSV 补。
# 导出文件的表头不固定（也可能带 BOM / GBK），所以按表头关键字打分识别，不认文件名。

CSV_KEYWORDS: dict[str, list[str]] = {
    "model": ["model", "模型"],
    "token": ["token", "tokens", "消耗", "用量", "额度", "积分", "credits"],
    "calls": ["调用", "次数", "count", "请求", "calls", "requests"],
    "session": ["session", "会话", "对话", "conversation"],
}

_CSV_MAX_BYTES = 50 * 1024 * 1024


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "ignore")


def _match_headers(header_line: str) -> tuple[list[str], dict[str, int]]:
    cells = [c.strip().strip('"') for c in header_line.split(",")]
    low = [c.lower() for c in cells]
    hits: dict[str, int] = {}
    for key, words in CSV_KEYWORDS.items():
        idx = -1
        for i, cell in enumerate(low):
            if any(w in cell for w in words):
                idx = i
                break
        hits[key] = idx
    return cells, hits


def score_csv(path: Path) -> dict | None:
    """读表头给一个 CSV 打分；不像用量明细则返回 None。"""
    try:
        st = path.stat()
        if st.st_size == 0 or st.st_size > _CSV_MAX_BYTES:
            return None
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return None

    first = ""
    for line in _decode_text(head).splitlines():
        if line.strip():
            first = line.strip()
            break
    if not first:
        return None

    cells, hits = _match_headers(first)
    score = 0
    if hits["model"] >= 0:
        score += 3
    if hits["token"] >= 0:
        score += 3
    if hits["calls"] >= 0:
        score += 1
    if hits["session"] >= 0:
        score += 1
    # 至少要能认出「模型」或「token」，否则不像用量明细
    if score < 3:
        return None

    return {
        "path": str(path),
        "name": path.name,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "headers": cells,
        "matched": {k: v for k, v in hits.items() if v >= 0},
        "score": score,
    }


def scan_csv_candidates(downloads: Path) -> list[dict]:
    if not downloads.is_dir():
        return []
    out: list[dict] = []
    try:
        entries = list(downloads.iterdir())
    except OSError:
        return []
    for f in entries:
        try:
            if not f.is_file() or f.suffix.lower() != ".csv":
                continue
        except OSError:
            continue
        s = score_csv(f)
        if s:
            out.append(s)
    out.sort(key=lambda x: (-x["score"], -x["mtime"]))
    return out


def safe_csv_path(raw: str) -> Path | None:
    """只允许读取下载目录内的 .csv，防止越权读取任意文件。"""
    if not raw:
        return None
    try:
        p = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    try:
        p.relative_to(default_downloads_dir().resolve())
    except ValueError:
        return None
    if p.suffix.lower() != ".csv" or not p.is_file():
        return None
    return p


# ---------- 自检 ----------

# 日志格式是 Trae 的内部实现，升级后可能变。自检的关键指标是「事件出现次数 vs 成功解析次数」：
# 出现了却一条都解析不出来，就说明格式改了。
_EVENT_LABELS: dict[str, tuple[str, str]] = {
    "snapshot": ("创建请求快照", EV_SNAPSHOT),
    "agentinfo": ("用户消息快照", EV_AGENTINFO),
    "mode": ("auto/manual 判定", EV_MODE),
    "report": ("响应回执", EV_REPORT),
}


def diagnose(log_root: Path) -> dict:
    parser = LogParser(log_root)
    files = parser.discover_files()
    events, _ = parser.load_all()

    parsed = {k: 0 for k in _EVENT_LABELS}
    for e in events:
        if e.kind in parsed:
            parsed[e.kind] += 1

    seen = {k: 0 for k in _EVENT_LABELS}
    for f in files:
        try:
            with open(f, "rb") as fh:
                for raw in fh:
                    line = raw.decode("utf-8", "ignore")
                    for k, (_, marker) in _EVENT_LABELS.items():
                        if marker in line:
                            seen[k] += 1
        except OSError:
            pass

    result = build_turns(events)
    return {
        "files": files,
        "seen": seen,
        "parsed": parsed,
        "turns": len(result["turns"]),
        "unmatched": len(result["unmatchedReports"]),
    }


def print_doctor(log_root: Path) -> int:
    print("Trae 用量监控 · 自检")
    print("=" * 48)

    print(f"日志根目录: {log_root}")
    if not log_root.is_dir():
        print("  不存在。确认 Trae CN 至少运行过一次，或用 --log-root 指定。")
        for alt in candidate_log_roots():
            if alt.is_dir():
                print(f"  提示：发现另一个可用目录 {alt}")
        return 1

    info = diagnose(log_root)
    files = info["files"]
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    print(f"  正常，日志文件 {len(files)} 个，共 {total / 1024 / 1024:.1f} MB")
    for f in files[:8]:
        try:
            print(f"    {f.relative_to(log_root)}  ({f.stat().st_size / 1024:.0f} KB)")
        except OSError:
            pass
    if len(files) > 8:
        print(f"    ... 其余 {len(files) - 8} 个省略")

    print("\n事件命中（出现次数 / 成功解析）:")
    broken: list[str] = []
    for k, (label, _) in _EVENT_LABELS.items():
        seen = info["seen"][k]
        ok = info["parsed"][k]
        note = ""
        if seen > 0 and ok == 0:
            note = "  <== 出现了但一条都没解析出来"
            broken.append(label)
        elif seen > ok:
            note = f"  ({seen - ok} 条未解析)"
        print(f"  {label:<14} {seen:>6} / {ok:<6}{note}")

    print(f"\n归因轮次: {info['turns']}（未归属响应 {info['unmatched']}）")

    ok_assets = (WEB_DIR / "index.html").is_file()
    print(f"\n静态资源: {WEB_DIR}  {'OK' if ok_assets else '缺失（打包时漏了 --add-data web）'}")

    if tray_available():
        print("系统托盘: 可用（pystray + Pillow 已安装）")
    else:
        print("系统托盘: 不可用（可选，需要 pystray pillow；不装也能用页面内实时面板）")

    print(f"提醒设置: {settings_path()}")

    off = detect_console(log_root)
    origin = "日志识别" if off["source"] == "log" else "未识别，退回官网首页"
    print(f"官方用量入口: {off['consoleUrl']}（{off['edition']} / {off['region']}，{origin}）")

    download_dir = default_downloads_dir()
    print(f"\n下载目录: {download_dir}")
    if download_dir.is_dir():
        cands = scan_csv_candidates(download_dir)
        print(f"  疑似用量明细 CSV: {len(cands)} 个")
        for c in cands[:5]:
            print(f"    {c['name']}（匹配度 {c['score']}）")
    else:
        print("  不存在")

    print()
    if broken:
        print("结论：有事件类型出现了却解析不出数据，Trae 很可能改了日志格式。")
        print("      把以上输出反馈给作者，trae_usage_monitor.py 顶部的事件常量需要更新。")
        return 2
    if info["turns"] == 0:
        print("结论：没解析到任何对话轮次。若确实用过 Trae，请反馈上面的输出。")
        return 2
    print("结论：解析正常。")
    return 0


# ---------- 系统托盘（可选增强） ----------
# 核心功能保持零依赖；托盘只有在装了 pystray + Pillow 时才启用，装不上就优雅降级成
# 「只用页面」。托盘自身通过本地 HTTP 接口取数，和页面完全解耦。


def tray_available() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _tray_image(color: tuple, badge: bool = False):
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, size - 4, size - 4), fill=color)
    d.ellipse((21, 21, size - 21, size - 21), fill=(11, 15, 22, 255))
    if badge:
        d.ellipse((40, 2, 62, 24), fill=(255, 107, 107, 255), outline=(11, 15, 22, 255), width=3)
    return img


def _http_json(url: str, method: str = "GET", payload: dict | None = None, timeout: float = 5.0) -> dict:
    import urllib.request

    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def tray_needs_main_thread() -> bool:
    """托盘是否必须在主线程运行。

    macOS 的 pystray 走 AppKit，Linux 走 GTK / AppIndicator，两者都硬性要求主线程；
    Windows 的消息循环在后台线程也能正常泵消息，因此保持原行为，
    以保住主线程 serve_forever 轮询带来的 Ctrl+C 即时响应。
    """
    return sys.platform != "win32"


def start_tray(port: int, stop_event: threading.Event, blocking: bool = False) -> bool:
    """启动托盘图标。blocking=True 时在当前（主）线程跑事件循环。返回是否成功装上。"""
    if not tray_available():
        return False
    try:
        import pystray
    except Exception:  # noqa: BLE001
        return False

    url = f"http://127.0.0.1:{port}"
    colors = {"ok": (51, 210, 161, 255), "warn": (255, 182, 72, 255), "bad": (255, 107, 107, 255)}
    state: dict = {"text": "今日 0 轮", "color": colors["ok"], "badge": False, "alertsOn": True}

    def open_dashboard(_icon=None, _item=None):
        webbrowser.open(url + "/")

    def toggle_alerts(icon, _item):
        try:
            state["alertsOn"] = not state["alertsOn"]
            _http_json(url + "/api/settings", "POST", {"alerts": {"enabled": state["alertsOn"]}})
            icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def quit_app(icon, _item):
        stop_event.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda _i: state["text"], open_dashboard, default=True),
        pystray.MenuItem("打开看板", open_dashboard),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda _i: f"用量提醒：{'已开启' if state['alertsOn'] else '已关闭'}", toggle_alerts
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出监控", quit_app),
    )
    icon = pystray.Icon("trae-usage-monitor", _tray_image(colors["ok"]), "Trae 用量监控", menu)

    def poll_loop():
        last_sig: tuple | None = None
        while not stop_event.is_set():
            try:
                live = _http_json(url + "/api/live")
                alerts = live.get("alerts") or []
                today = (live.get("today") or {}).get("turns", 0)
                last = live.get("last") or {}
                cfg = live.get("settings") or {}
                state["alertsOn"] = bool(cfg.get("alertsEnabled", True))

                if any(a.get("level") == "bad" for a in alerts):
                    state["color"], state["badge"] = colors["bad"], True
                elif alerts:
                    state["color"], state["badge"] = colors["warn"], True
                else:
                    state["color"], state["badge"] = colors["ok"], False

                text = f"今日 {today} 轮"
                if last:
                    text += f" · 最近 {last.get('model', '')}"
                if alerts:
                    text += f" · {len(alerts)} 条提醒"
                state["text"] = text

                icon.icon = _tray_image(state["color"], state["badge"])
                icon.title = "Trae 用量监控 · " + text
                try:
                    icon.update_menu()
                except Exception:  # noqa: BLE001
                    pass

                sig = tuple(sorted(str(a.get("code", "")) + str(a.get("sessionId") or "") for a in alerts))
                if sig and sig != last_sig and cfg.get("trayNotify"):
                    try:
                        icon.notify(str(alerts[0].get("detail", "")), "Trae 用量提醒")
                    except Exception:  # noqa: BLE001
                        pass
                last_sig = sig
            except Exception:  # noqa: BLE001
                state["text"] = "等待看板服务…"
            stop_event.wait(5)

    threading.Thread(target=poll_loop, name="tray-poll", daemon=True).start()

    if blocking:
        # 主线程跑事件循环，直到托盘「退出监控」被点击或收到信号
        try:
            icon.run()
        except Exception as exc:  # noqa: BLE001
            _stderr(f"[tray] 托盘不可用，已降级为仅页面模式：{exc}\n")
            stop_event.set()
            return False
        stop_event.set()
        return True

    def runner():
        try:
            icon.run()
        except Exception as exc:  # noqa: BLE001
            _stderr(f"[tray] 托盘不可用，已降级为仅页面模式：{exc}\n")

    threading.Thread(target=runner, name="tray", daemon=True).start()
    return True


# ---------- HTTP 服务 ----------

_PAYLOAD_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
# 实时面板是秒级轮询，缓存 TTL 跟着压到 2 秒；逐文件解析结果本身有缓存，没新日志时几乎零成本。
_CACHE_TTL = 2.0


class Handler(BaseHTTPRequestHandler):
    log_root: Path = default_log_root()
    quiet: bool = False

    def log_message(self, fmt, *args):  # noqa: A002
        if not self.quiet:
            _stderr("[http] %s\n" % (fmt % args))

    def _send(self, code: int, body: bytes, ctype: str, gzip_ok: bool = False):
        headers = [("Content-Type", ctype)]
        if gzip_ok and len(body) > 512 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body, 6)
            headers.append(("Content-Encoding", "gzip"))
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == "/api/data":
            try:
                payload = self._data()
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"error": str(exc)})
                return
            days = parse_qs(route.query).get("days", ["0"])[0]
            if days.isdigit() and int(days) > 0:
                cutoff = (datetime.now().astimezone() - timedelta(days=int(days))).timestamp()
                payload = dict(payload)
                payload["turns"] = [t for t in payload["turns"] if t["ts"] >= cutoff]
                payload["unmatchedReports"] = [
                    r for r in payload["unmatchedReports"] if r["ts"] >= cutoff
                ]
                payload["filteredDays"] = int(days)
            self._send_json(200, payload)
            return

        if route.path == "/api/live":
            try:
                body = live_payload(self._data(), load_settings())
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"error": str(exc)})
                return
            self._send_json(200, body)
            return

        if route.path == "/api/settings":
            if parse_qs(route.query).get("defaults", ["0"])[0] == "1":
                self._send_json(200, DEFAULT_SETTINGS)
            else:
                self._send_json(200, load_settings())
            return

        if route.path == "/api/csv/candidates":
            downloads = default_downloads_dir()
            try:
                cands = scan_csv_candidates(downloads)
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"error": str(exc)})
                return
            self._send_json(200, {"downloads": str(downloads), "exists": downloads.is_dir(), "candidates": cands})
            return

        if route.path == "/api/csv/read":
            path = safe_csv_path(parse_qs(route.query).get("path", [""])[0])
            if path is None:
                self._send_json(400, {"error": "路径不在下载目录内或不是 CSV"})
                return
            try:
                raw = path.read_bytes()
            except OSError as exc:
                self._send_json(500, {"error": str(exc)})
                return
            self._send_json(200, {"name": path.name, "text": _decode_text(raw)})
            return

        name = route.path.lstrip("/") or "index.html"
        ctype_map = {
            "index.html": "text/html; charset=utf-8",
            "app.js": "application/javascript; charset=utf-8",
            "app.css": "text/css; charset=utf-8",
            "favicon.ico": "image/x-icon",
        }
        if name not in ctype_map:
            self._send(404, b"not found", "text/plain")
            return
        path = WEB_DIR / name
        if not path.is_file():
            self._send(404, b"missing web asset", "text/plain")
            return
        # ico 已是压缩格式，再 gzip 只是白费 CPU
        self._send(200, path.read_bytes(), ctype_map[name], gzip_ok=name != "favicon.ico")

    def do_POST(self):
        route = urlparse(self.path)
        if route.path != "/api/settings":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if 0 < length <= 1_000_000 else b""
        try:
            patch = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "请求体不是合法 JSON"})
            return
        self._send_json(200, save_settings(patch if isinstance(patch, dict) else {}))

    def _data(self) -> dict:
        key = str(self.log_root)
        with _CACHE_LOCK:
            hit = _PAYLOAD_CACHE.get(key)
            if hit and time.time() - hit[0] < _CACHE_TTL:
                return hit[1]
        payload = snapshot_payload(self.log_root)
        with _CACHE_LOCK:
            _PAYLOAD_CACHE[key] = (time.time(), payload)
        return payload


def serve(log_root: Path, port: int, open_browser: bool, tray: bool = True) -> None:
    Handler.log_root = log_root
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Trae 用量看板: {url}")
    print(f"日志目录: {log_root}")
    if not log_root.is_dir():
        print("!! 日志目录不存在，请确认 Trae CN 已运行过，或用 --log-root 指定")

    stop_event = threading.Event()
    tray_ok = bool(tray) and tray_available()

    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    if not tray_ok:
        # 托盘没起来（--no-tray，或可选依赖缺失）。静默打包下这时就只剩一个没有任何
        # 交互入口的后台进程了，所以补一个控制台：能看输出，也能 Ctrl+C / 关窗口退出。
        _ensure_console()
        if tray:
            print("系统托盘: 未启用（可选依赖缺失，安装后可用：pip install pystray pillow）")
        else:
            print("系统托盘: 已按 --no-tray 关闭")
        print("已切换为控制台模式，按 Ctrl+C 退出。")

    if tray_ok and tray_needs_main_thread():
        # 托盘必须在主线程（macOS 的 AppKit / Linux 的 GTK 都硬性要求主线程），
        # 所以把 HTTP 服务放到后台线程，主线程交给托盘事件循环。
        def run_http() -> None:
            try:
                httpd.serve_forever()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=run_http, name="http", daemon=True).start()
        print("系统托盘: 已启用（退出监控请用托盘菜单，或按 Ctrl+C）")
        try:
            if not start_tray(port, stop_event, blocking=True):
                stop_event.wait()  # 托盘没起来，退化为纯后台服务，等 Ctrl+C
        except KeyboardInterrupt:
            print("\n已停止")
        finally:
            stop_event.set()
            httpd.shutdown()
            httpd.server_close()
        return

    if tray_ok and start_tray(port, stop_event):
        print("系统托盘: 已启用（退出监控请用托盘菜单，或按 Ctrl+C）")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        stop_event.set()
        httpd.server_close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Trae 用量与模型归因监控")
    ap.add_argument("--log-root", type=Path, default=default_log_root(), help="日志根目录")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--json", action="store_true", help="打印解析结果 JSON 后退出")
    ap.add_argument("--out", type=Path, help="导出解析结果到文件")
    ap.add_argument("--doctor", action="store_true", help="自检：检查日志格式与资源是否正常")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--no-tray", action="store_true", help="不启用系统托盘")
    args = ap.parse_args()

    if args.doctor:
        _ensure_console()
        return print_doctor(args.log_root)

    if args.json or args.out:
        _ensure_console()
        payload = snapshot_payload(args.log_root)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(f"已导出 {args.out}")
        if args.json:
            print(text)
        return 0

    if _port_in_use(args.port):
        _report_fatal(
            f"端口 {args.port} 已被占用。\n\n"
            f"如果本程序已经在运行（看右下角托盘图标），直接用那个就行；\n"
            f"否则请先关掉占用该端口的程序，或用 --port 换一个端口重试。",
            error=True,
        )
        return 1

    try:
        serve(args.log_root, args.port, not args.no_open, tray=not args.no_tray)
    except OSError as exc:
        # 兜底：探测和 bind 之间仍有极小的竞争窗口，或者端口被别的东西占着。
        _report_fatal(
            f"启动失败：{exc}\n\n"
            f"端口 {args.port} 可能已被占用。\n"
            f"请先关掉已运行的实例，或用 --port 换一个端口重试。",
            error=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
