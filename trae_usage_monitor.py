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


# ---------- HTTP 服务 ----------

_PAYLOAD_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 5.0


class Handler(BaseHTTPRequestHandler):
    log_root: Path = default_log_root()
    quiet: bool = False

    def log_message(self, fmt, *args):  # noqa: A002
        if not self.quiet:
            sys.stderr.write("[http] %s\n" % (fmt % args))

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == "/api/data":
            qs = parse_qs(route.query)
            days = qs.get("days", ["0"])[0]
            try:
                payload = self._data()
            except Exception as exc:  # noqa: BLE001
                self._send(500, json.dumps({"error": str(exc)}).encode(), "application/json")
                return
            if days.isdigit() and int(days) > 0:
                cutoff = (datetime.now().astimezone() - timedelta(days=int(days))).timestamp()
                payload = dict(payload)
                payload["turns"] = [t for t in payload["turns"] if t["ts"] >= cutoff]
                payload["unmatchedReports"] = [
                    r for r in payload["unmatchedReports"] if r["ts"] >= cutoff
                ]
                payload["filteredDays"] = int(days)
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return

        if route.path == "/api/csv/candidates":
            downloads = default_downloads_dir()
            try:
                cands = scan_csv_candidates(downloads)
            except Exception as exc:  # noqa: BLE001
                self._send(500, json.dumps({"error": str(exc)}).encode(), "application/json")
                return
            body = {
                "downloads": str(downloads),
                "exists": downloads.is_dir(),
                "candidates": cands,
            }
            self._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return

        if route.path == "/api/csv/read":
            qs = parse_qs(route.query)
            path = safe_csv_path(qs.get("path", [""])[0])
            if path is None:
                self._send(400, json.dumps({"error": "路径不在下载目录内或不是 CSV"}).encode(), "application/json")
                return
            try:
                raw = path.read_bytes()
            except OSError as exc:
                self._send(500, json.dumps({"error": str(exc)}).encode(), "application/json")
                return
            body = {"name": path.name, "text": _decode_text(raw)}
            self._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return

        name = route.path.lstrip("/") or "index.html"
        if name not in ("index.html", "app.js", "app.css"):
            self._send(404, b"not found", "text/plain")
            return
        path = WEB_DIR / name
        if not path.is_file():
            self._send(404, b"missing web asset", "text/plain")
            return
        ctype = {
            "index.html": "text/html; charset=utf-8",
            "app.js": "application/javascript; charset=utf-8",
            "app.css": "text/css; charset=utf-8",
        }[name]
        self._send(200, path.read_bytes(), ctype)

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


def serve(log_root: Path, port: int, open_browser: bool) -> None:
    Handler.log_root = log_root
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Trae 用量看板: {url}")
    print(f"日志目录: {log_root}")
    if not log_root.is_dir():
        print("!! 日志目录不存在，请确认 Trae CN 已运行过，或用 --log-root 指定")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.server_close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Trae 用量与模型归因监控")
    ap.add_argument("--log-root", type=Path, default=default_log_root(), help="日志根目录")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--json", action="store_true", help="打印解析结果 JSON 后退出")
    ap.add_argument("--out", type=Path, help="导出解析结果到文件")
    ap.add_argument("--doctor", action="store_true", help="自检：检查日志格式与资源是否正常")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    if args.doctor:
        return print_doctor(args.log_root)

    if args.json or args.out:
        payload = snapshot_payload(args.log_root)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(f"已导出 {args.out}")
        if args.json:
            print(text)
        return 0

    serve(args.log_root, args.port, not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
