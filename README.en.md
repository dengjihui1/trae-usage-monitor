# Trae Usage Monitor · Auto Model Attribution

English | [中文](README.md)

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-0078d4.svg)](#cross-platform-notes)
[![Dependencies](https://img.shields.io/badge/Dependencies-zero-brightgreen.svg)](#privacy-and-security)
[![Release](https://img.shields.io/github/v/release/dengjihui1/trae-usage-monitor?color=blue)](https://github.com/dengjihui1/trae-usage-monitor/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/dengjihui1/trae-usage-monitor/total)](https://github.com/dengjihui1/trae-usage-monitor/releases)

Parses Trae's local logs to **recover the model each conversation turn actually landed on**, aggregates call volume per model, and renders it as a local dashboard.

![Dashboard overview](docs/dashboard.png)

> **The one-line pitch**: in Auto mode Trae picks the model for you, and the UI only ever shows "Auto". This tool recovers the model Auto actually chose, **turn by turn**. Existing open-source tools (TraeUsage, hydee-trae-ai-stats, ...) only report quota or estimate tokens — none of them do routing attribution.

---

## What it is

Trae's Auto mode routes between the models available to you, but all you see is an "Auto" label. That leaves two questions unanswered:

- Of my 100 turns, how many used the expensive model and how many the cheap one?
- This answer was bad — was it routed to a weak model?

This tool digs the answer out of the local logs: **which model each turn used, whether Auto picked it or you did, how long it took, and whether it succeeded** — then aggregates it into a dashboard.

**What it does not do**: it does not check remaining quota (that is not a differentiator, the official console already has it), does not modify the Trae client, and does not touch the network.

---

## Features

### Live usage panel + usage alerts (new in v1.1)

A **second-by-second** panel pinned to the top of the page: today's turn count, the currently active session, the model the latest turn landed on, today's success rate, and a 24-turn model stream (colour = model, dot = response status).

![Live usage panel](docs/live.png)

**Usage alerts** are a set of threshold rules you control. When a rule fires, an alert bar appears at the top of the page, and you can optionally get a desktop notification / tray balloon as well:

| Rule | Default | Meaning |
| --- | --- | --- |
| Today's turns over threshold | 300 | Cumulative turns for the day exceed the threshold |
| Single-session turns over threshold | 80 | One session exceeds the threshold today |
| Failure rate over threshold | 20% (≥5 samples) | Too many turns with a definite receipt failed |
| Average latency over threshold | off | Average response time is too high |
| Auto landed on a watched model | off | Auto routed to a model you care about |

> **Why "usage alerts" and not "quota alerts"?**
> The local logs contain **no quota balance and no token values**, so a true quota alert is impossible. Inferring quota from turn counts would be seriously misleading — model prices differ by orders of magnitude, so 100 Auto turns and 100 manual turns on an expensive model are nowhere near the same cost. So the tool only alerts on **usage signals that are genuinely visible locally** (turns / latency / failure rate / model routing). Every number comes from real logs; nothing is guessed.

Alerts are **state-based**, not event-based: while the condition holds, the alert stays; when it clears, the alert disappears. Reloading or reopening the page never stacks duplicates.

### Auto decision trace (the core)

Per session, every turn's model is laid out as a timeline. **A colour change means Auto switched models**, and the switch point is labelled.

![Auto decision trace](docs/dashboard.png)

A checkbox in the top-right also shows manual turns as dashed chips. Click any row to filter the detail table below.

### Model distribution · mode split · status

See at a glance how concentrated your model usage is, the auto vs. manual ratio, and the success rate.

![Model distribution and mode split](docs/analytics.png)

### Daily trend

Stacked bars of turns per model per day, useful for spotting how your habits change.

![Daily trend](docs/trend.png)

### Session list

Click a row to filter the detail table. The first column supports ✎ rename (stored in your browser).

![Session list](docs/sessions.png)

### Detail table

Time / session / model / mode / status / latency / agent, filterable by model and mode.

![Detail table](docs/turns.png)

### Token usage import

Exact token counts are not in the local logs, so you can import the console's "usage details" CSV to fill the gap — with auto-detection.

![Token usage panel](docs/token.png)

---

## Quick start

Three ways. Pick whichever fits.

### Option 1: use the exe (no Python needed)

1. **[Download `trae-usage-monitor.exe`](https://github.com/dengjihui1/trae-usage-monitor/releases/latest/download/trae-usage-monitor.exe)** (or pick a version from this repo's Releases page)
2. Double-click it
3. The dashboard opens in your browser. The exe itself **sits silently in the system tray** — no console window flashes up.

> On first run Windows may show a blue "Windows protected your PC" prompt — the exe is not code-signed (most personal projects are not). Click "**More info → Run anyway**".

**To quit**: right-click the tray icon → "退出监控" (Quit).
(If the tray is not active — it cannot attach on this machine, or you passed `--no-tray` — the exe attaches a console window instead, so you can `Ctrl+C` out or just close that window; it will never leave you with an unkillable silent process.)

**Double-clicking twice will not crash it**: on the second launch the port is already taken, so it pops up a dialog telling you an instance is already running instead of dying with a traceback.

### Option 2: run with Python (recommended)

Python 3.10+ required. **The core needs no third-party packages** (the system tray is an optional add-on, see below).

```bash
git clone https://github.com/dengjihui1/trae-usage-monitor.git
cd trae-usage-monitor
python trae_usage_monitor.py
```

On Windows you can also just double-click `start.bat`; on macOS, double-click `start.command`.

The browser opens <http://127.0.0.1:8788/> automatically.

### Option 3: build the binary from source

```bash
build.bat            # Windows
./build.sh           # macOS / Linux
```

This produces a single-file `dist\trae-usage-monitor.exe` on Windows (on macOS / Linux: `dist/trae-usage-monitor`).

### Command-line flags

```bash
python trae_usage_monitor.py --port 9000          # change port
python trae_usage_monitor.py --no-open            # don't open the browser
python trae_usage_monitor.py --no-tray            # don't start the system tray
python trae_usage_monitor.py --json               # print the parsed result
python trae_usage_monitor.py --out data.json      # export a snapshot
python trae_usage_monitor.py --doctor             # self-check
python trae_usage_monitor.py --log-root "D:\logs" # point at a log root manually
```

### System tray (optional add-on)

The core has **zero third-party dependencies**. The system tray is optional: if `pystray` + `Pillow` are installed it turns on automatically; if not, the tool silently degrades to "in-page live panel only" with no other impact.

```bash
pip install pystray pillow
```

> On macOS `pystray` pulls in the required `pyobjc-framework-Quartz` automatically. On Linux the tray uses GTK / AppIndicator; if those libraries are missing the tray simply fails to attach and degrades silently — the page keeps working.

Once enabled, an icon appears in your taskbar:

- **Icon colour = current state**: green normal / yellow has alerts / red has failure-class alerts, with a red badge dot
- **Hover**: `今日 N 轮 · 最近 <model> · M alerts`
- **Left click / first menu item**: open the dashboard
- **Menu "用量提醒"**: toggle alerts on/off (shared with the page)
- **Menu "退出监控"**: shut down the whole server

> The tray and the page are decoupled: the tray polls the local `127.0.0.1` `/api/live` endpoint, so it works whether or not the page is open.

---

## Deployment guide

### 1. Keep it running on your own machine

The simplest setup: leave it running in the background and open <http://127.0.0.1:8788/> when you need it.

**Windows**

Right-click `start.bat` → Create shortcut, then drop the shortcut into your Startup folder (press <kbd>Win</kbd>+<kbd>R</kbd>, type `shell:startup`). It will start in the background on every boot.

To run it completely invisibly, create `start-hidden.vbs`:

```vbscript
CreateObject("WScript.Shell").Run "pythonw trae_usage_monitor.py --no-open", 0, False
```

**macOS / Linux**

On macOS you can just double-click `start.command`. To keep it running in the background, use `--no-open`:

```bash
nohup python3 trae_usage_monitor.py --no-open > /dev/null 2>&1 &
```

### 2. Package and distribute

**Windows**

```bash
build.bat
```

**macOS / Linux**

```bash
chmod +x build.sh   # first time only
./build.sh
```

The script installs PyInstaller and the optional tray deps for you; the output lands in `dist/` (on macOS / Linux: a single `dist/trae-usage-monitor` executable). Whoever receives the binary **does not need Python installed**.

> **PyInstaller does not cross-compile**: you cannot build a macOS binary on Windows or vice versa. Build on the platform you want to ship for.
> The equivalent raw command, if you prefer not to use the script (note the `--add-data` separator: `:` on macOS / Linux, `;` on Windows):
>
> ```bash
> pyinstaller --onefile --name trae-usage-monitor --add-data "web:web" trae_usage_monitor.py
> ```

> How packaging works: the `web/` directory is bundled via `--add-data`, and at startup the program reads the static assets from PyInstaller's unpack directory (`sys._MEIPASS`). If you move `web/`, update `--add-data` to match.

### 3. Publish to GitHub Releases

1. Make sure the code is pushed:

   ```bash
   git add .
   git commit -m "release: v1.0.0"
   git push
   ```

2. Tag and push the tag:

   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

3. Open your repo on GitHub → **Releases** → **Draft a new release** → pick the tag you just pushed → drag `dist/trae-usage-monitor.exe` into the attachments area → add release notes → publish.

After that the download link in the README works.

---

## Self-check and troubleshooting

The log format is a Trae internal implementation and **may change between versions**. So there is a built-in self-check:

```bash
python trae_usage_monitor.py --doctor
```

Sample output:

```
Trae 用量监控 · 自检
================================================
日志根目录: C:\Users\you\AppData\Roaming\Trae CN\logs
  正常，日志文件 12 个，共 26.5 MB

事件命中（出现次数 / 成功解析）:
  创建请求快照         612 / 612
  用户消息快照          48 / 48
  auto/manual 判定     612 / 612
  响应回执             605 / 605

归因轮次: 612（未归属响应 0）
静态资源: C:\...\_MEIPASS\web  OK
系统托盘: 可用（pystray + Pillow 已安装）
提醒设置: C:\Users\you\.trae-usage-monitor\settings.json
下载目录: C:\Users\you\Downloads
  疑似用量明细 CSV: 0 个

结论：解析正常。
```

**How to read it**

| Symptom | Meaning | Action |
| --- | --- | --- |
| "seen" ≈ "parsed" for every event | Healthy | Nothing to do |
| An event shows "seen but nothing parsed" | Trae changed the log format | Report the output to the author |
| `归因轮次` (turns) is 0 | Nothing parsed at all | Same, and confirm Trae has actually been used |
| `静态资源: 缺失` | Packaging missed `--add-data web` | Rebuild |
| `系统托盘: 不可用` | Optional `pystray` / `Pillow` not installed | `pip install pystray pillow` if you want the tray; everything else works without it |

Unrecognized lines are **always skipped silently** — a format change degrades the statistics, it never crashes the tool.

If the format really did change, the fix is small: the four event constants at the top of `trae_usage_monitor.py` (`EV_SNAPSHOT` / `EV_AGENTINFO` / `EV_MODE` / `EV_REPORT`) are the only strings you need to update, guided by the `--doctor` output.

---

## Data source and internals

Log locations (auto-detected, in priority order):

| Platform | Path |
| --- | --- |
| Windows | `%APPDATA%\Trae CN\logs\<startup time>\window<n>\renderer*.log` |
| macOS | `~/Library/Application Support/Trae CN/logs/...` |
| Linux | `~/.config/Trae CN/logs/...` |

If none of those exist, sibling directories matching `Trae*` are scanned as a fallback (the folder name differs by distribution: `Trae` / `Trae CN` / `Trae Solo`).

Three kinds of log events are used:

| Event | Purpose | Key fields |
| --- | --- | --- |
| `created chat request selection snapshot` | anchor for one turn | sessionId, messageId, agentId, agentName, modelName, customModelProvider |
| `model auto selection resolved` | whether this turn was auto or manual | sessionId, agentId, strategy(auto/manual), source |
| `reportFrontResponse, status: ...` | result receipt for the turn | model, costTime, errCode, traceId, tokenOutputInterval |

**Attribution rules**

- A snapshot is matched to the **nearest-in-time** `mode` event by `sessionId + agentId` to decide auto vs. manual.
- The `reportFrontResponse` event **carries no sessionId**, so its model name is normalised (`custom_responses_compatible//gpt-6.1-sol` → `gpt-6.1-sol`) and matched against the nearest still-unmatched snapshot.
- Receipts that cannot be matched are counted separately as "**unattributed responses**" instead of being forced onto a session — better to leave a gap than to give you wrong data.

---

## Privacy and security

Tools that read local logs have to state this up front:

- **Local only, no network** — it reads your local Trae logs and makes no network requests, no telemetry, no uploads.
- **Binds to `127.0.0.1` only** — the dashboard HTTP server listens on the loopback address, so other devices on your LAN cannot reach it.
- **No conversation content is extracted** — only the lines carrying the event markers above and the JSON metadata that follows (session id, model name, latency, status, error code, ...) are kept. Your chat content is never stored, displayed or exported.
- **Zero third-party dependencies (core)** — parsing, attribution, the HTTP server and the front end use only the Python standard library and native web tech. No pip packages, no supply-chain surface. The system tray is the one optional add-on and only turns on if you install `pystray pillow` yourself.
- **Readable, deletable settings** — alert thresholds live in `~/.trae-usage-monitor/settings.json` (on Windows: `C:\Users\<you>\.trae-usage-monitor\settings.json`) as plain JSON, ready to inspect or delete.
- **Auditable** — the whole tool is `trae_usage_monitor.py` plus three front-end files. Read them.

Precise boundary of the "auto-find usage CSV" feature:

- **Scan phase**: reads only the **first 64 KB** of each `.csv` in the downloads folder, to inspect the header. It does not read whole files and does not touch non-CSV files.
- **Import phase**: reads the **single file you selected** in full, and that file must live inside the downloads folder (out-of-bounds paths are rejected by the backend).
- No directory other than the downloads folder is read.

---

## Where session names come from

The short version: **session titles are not available locally**.

Checked: `globalStorage/state.vscdb` (its `chat.ChatSessionStore.index` is empty), `workspaceStorage/*/state.vscdb`, `ModularData/ai-agent/database.db` (encrypted, not readable SQLite), and every log line — none of them carry a session title field. **Titles live in the cloud only.**

So there are two fallback layers:

1. **Default name = agent name.** The `created empty user message selection snapshot` event carries an `agentName`, matched to each turn by `sessionId + agentId`.
2. **Manual rename.** Each row in the session table has a ✎ button; names are stored in the browser's `localStorage`, keyed by sessionId, and survive a restart.

---

## Token usage (important)

**Exact token counts are not available locally**: `token_usage` / `context_usage` only log the event name with no payload, and `ModularData/ai-agent/database.db` is encrypted.

Exact tokens must come from the console: **Personal usage → Usage details**, exported as CSV. The "Token usage" panel offers two import paths:

1. **Auto-find** — click the auto-find button; the tool scans the system downloads folder (Windows `%USERPROFILE%\Downloads`, macOS/Linux `~/Downloads`), scores files by **header keywords** (model / 模型, token / 消耗 / 用量, 调用 / 次数, session / 会话) and imports the best match. If nothing matches it says so and you can fall back to option 2.
2. **Manual pick** — choose a CSV from anywhere.

File name and encoding (UTF-8 / GBK / UTF-16, with or without BOM) do not affect detection. After import, local turns are merged by model name into a "local turns / console call count / token usage" comparison table.

---

## FAQ

<details>
<summary><b>The dashboard is empty / shows 0 turns</b></summary>

Run `python trae_usage_monitor.py --doctor` first.

- "log root does not exist" → Trae has never run, or is installed in a non-default location; point at it with `--log-root`.
- An event shows "seen but nothing parsed" → Trae changed the log format; report the `--doctor` output.
- Everything healthy but still 0 turns → you may have never used Auto mode; no event, no data.

</details>

<details>
<summary><b>Why is the turn count lower than the number of messages I see in Trae?</b></summary>

Only turns that actually issued a model call are counted. A message you sent and then aborted, or a tool action that triggered no model request, does not count.

</details>

<details>
<summary><b>There are a dozen "unattributed responses" — is that a bug?</b></summary>

No, it is a deliberate, conservative design. The `reportFrontResponse` event carries no sessionId, so it is only matched to a turn by "same model name + nearest in time". When that guess is not confident, the receipt is left unattributed rather than forced onto a session, to avoid giving you wrong data.

Truncated rotated logs, or several sessions using the same model at the same time, both push this number up.

</details>

<details>
<summary><b>Session names are all agent names — can I get my real titles?</b></summary>

No, because the data does not exist locally (see "Where session names come from"). You can rename manually; the name is stored locally.

</details>

<details>
<summary><b>Why can't the alert warn me that my quota is almost used up?</b></summary>

Because quota balance simply does not exist locally. The logs carry no balance, no token values and no pricing rules, and the console API needs credentials. So the tool sticks to **usage alerts built on locally visible signals** (today's turns, single-session turns, failure rate, average latency, whether Auto hit a watched model).

A real quota alert would only be possible by talking to the official console API, which would require your credentials — breaking the "local only, no credentials" premise. Not doing that for now.

</details>

<details>
<summary><b>An alert never fires / never goes away?</b></summary>

- **Never fires**: open "提醒设置" and check that the master switch and the specific rule are both on, and that the threshold is not too high (the default "today's turns" is 300, which you will rarely hit day to day — lower it to test).
- **Never goes away**: alerts are state-based, so while the condition holds they stay — "today's turns over threshold" only clears at midnight. To clear it immediately, raise the threshold.
- **No desktop notification**: you need to authorise it once via "开启浏览器通知" on the page; `127.0.0.1` counts as a secure context, so it normally works. Tray balloons additionally require the tray to be enabled.

Settings live in `~/.trae-usage-monitor/settings.json`; delete that file to return to defaults.

</details>

<details>
<summary><b>Can it show how much money / credits I spent?</b></summary>

Not directly. There are no token values and no pricing rules locally. You can import the console CSV to see the official usage numbers.

</details>

<details>
<summary><b>Windows says "Windows protected your PC", or antivirus flags it</b></summary>

Because the exe is not code-signed (personal open-source projects generally are not). Click "More info → Run anyway". If you would rather not trust a binary, run it from source — it is one file and takes seconds to read.

</details>

<details>
<summary><b>Can I see history from another device?</b></summary>

No. Logs are local, and the tool only reads the local machine.

</details>

---

## Cross-platform notes

- **Windows**: fully supported; `start.bat` / `build.bat` work as-is, and the system tray is verified working.
- **macOS**: launch with `start.command`, package with `build.sh`. **The tray is handled the way macOS requires** — AppKit demands that the event loop run on the main thread, so at startup the HTTP server is moved to a background thread and the main thread is handed to the tray. The tray is therefore expected to work on macOS.
- **Linux**: package with `build.sh`; the tray uses GTK / AppIndicator and is similarly handled for the main-thread requirement. If GTK is missing, it degrades silently to page-only mode.
- **Honest caveat**: none of the macOS / Linux code paths have been **verified on real hardware** (the dev machine is Windows). Log directories follow the VS Code convention (macOS `~/Library/Application Support/Trae CN/logs`). If you get it running on a Mac / Linux box — or fail to — please open an issue; the log path is the most likely thing to be off.
- **Tray degradation is normal, not exceptional**: on any platform, if the tray cannot attach, the page keeps working, and `--no-tray` turns it off explicitly.

---

## Project structure

```
trae_usage_monitor.py   parser + attribution + alert evaluation + HTTP server (single file, zero-dep core)
web/index.html          dashboard markup
web/app.css             styles
web/app.js              aggregation, rendering, live panel, alert settings, CSV import
start.bat               one-click launch (Windows)
start.command           one-click launch (macOS, double-click)
build.bat               build a single-file exe (Windows, includes the optional tray deps)
build.sh                build a single-file binary (macOS / Linux)
docs/                   README screenshots
README.md               Chinese readme
README.en.md            this file
LICENSE                 MIT
.gitignore              build artifacts
```

At runtime a settings file is created in your home directory (safe to delete):

```
~/.trae-usage-monitor/settings.json    alert thresholds and refresh intervals
```

---

## Known limitations

- **No exact tokens** — the local logs have no token values; import the console CSV instead.
- **No quota balance** — hence "usage alerts", not "quota alerts" (see above).
- **No session titles** — titles are cloud-only; the default is the agent name and can be renamed manually.
- **Depends on the log format** — it is a Trae internal implementation and may change; `--doctor` diagnoses it quickly.
- **This machine only** — logs are local, so another device is invisible.
- **Auto attribution only** — no quota lookup, no cost conversion.
- **Tray is optional** — needs `pystray` + `Pillow` (macOS pulls in pyobjc automatically); if it fails to attach it degrades to page-only mode.

---

## Contributing

Issues are welcome — please attach the full `--doctor` output, which is the fastest way to tell whether the log format changed.

## License

[MIT](LICENSE) © 2026 Trae Usage Monitor contributors

> This tool is not affiliated with Trae / ByteDance in any way. It is an independent third-party open-source project.
