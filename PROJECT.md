# Trae 用量监控 · 项目完整说明（交接给 GPT 用）

> 这份文档的目的：把整个项目的**文件位置、结构、运行方式、内部原理和已完成/未完成的边界**一次性讲清楚。
> 文档里的所有路径都是本机绝对路径，行数/体积来自实际测量（2026-11-02）。

---

## 0. 一句话定位

解析 **Trae 客户端（VS Code 系）的本地日志**，把「每一轮对话实际落到了哪个模型」还原出来 ——
因为 Auto 模式下界面永远只显示「Auto」，用户看不到真实的模型路由。

**差异化**：现有开源工具（TraeUsage、hydee-trae-ai-stats 等）都只统计额度或估算 token，**没有做路由归因**。
本项目只做归因 + 用量可视化，**不查额度**（那不是差异化，官方控制台已经有了）——
只提供一个「查看官方用量」按钮，从日志里认出用户所属的官方控制台域名后一键跳转。

- 仓库：`https://github.com/dengjihui1/trae-usage-monitor`（public）
- 本地路径：`D:\Trae\trae-usage-monitor`
- 默认端口：`8788`，只监听 `127.0.0.1`
- 技术栈：**Python 3.10+ 标准库**（核心零第三方依赖）+ 原生 HTML/CSS/JS
- 许可：MIT

---

## 1. 文件结构与职责

```
D:\Trae\trae-usage-monitor\
├── trae_usage_monitor.py     1516 行 / 55122 B   ★ 全部后端逻辑（单文件）
├── web\                                          ★ 前端（零构建、无框架）
│   ├── index.html             171 行 /  6484 B
│   ├── app.js                 999 行 / 36411 B
│   ├── app.css                408 行 / 13248 B
│   └── favicon.ico                    27128 B   多尺寸图标，兼作 exe 图标
├── docs\                                         README 用的截图
│   ├── dashboard.png / dashboard-full.png / analytics.png / trend.png
│   ├── sessions.png / turns.png / token.png / live.png
├── build.bat                   33 行 /  1290 B   Windows 打包（纯 ASCII + CRLF）
├── build.sh                    32 行 /  1407 B   macOS / Linux 打包（LF）
├── start.bat                    4 行 /    62 B   Windows 双击启动
├── start.command                6 行 /   265 B   macOS 双击启动（LF，可执行位 100755）
├── README.md                  506 行 / 24458 B   中文说明
├── README.en.md               506 行 / 25206 B   英文说明
├── PROJECT.md                 335 行 / 20052 B   本文件：项目全景，供 AI / 新同事快速接手
├── .gitattributes              11 行 /   306 B   固定换行符（*.sh/*.command=LF，*.bat=CRLF，二进制不转）
├── .gitignore                  12 行 /   146 B   排除 __pycache__/ build/ dist/ *.spec *.log data.json
└── LICENSE                     21 行 /  1088 B   MIT
```

### `trae_usage_monitor.py` 内部分区（按行号）

| 行号 | 内容 |
| --- | --- |
| 34–56 | `_base_dir()`（兼容 PyInstaller `sys._MEIPASS`）、`WEB_DIR`、四个日志事件常量、`_APP_DIRS` |
| 59–140 | **Windows 打包护栏**：`_stderr`(59) / `_ensure_console`(73) / `_message_box`(102) / `_report_fatal`(115) / `_port_in_use`(127) |
| 142–225 | 平台目录探测与模型名归一化：`_config_base`(142) / `candidate_log_roots`(154) / `default_log_root`(170) / `default_downloads_dir`(178) / `_json_tail`(187) / `_parse_ts`(200) / `normalize_model`(209) / `short_model`(219) |
| 226–344 | 解析层：`RawEvent`(227) / `FileCache`(235) / `LogParser`(241，含 `discover_files` 249 / `parse_file` 264 / `load_all` 316) / `_run_key`(330) / `_win_key`(334) / `_rot_key`(339) |
| 346–497 | **归因核心**：`build_turns`(349) / `mode_for`(390) |
| **499–583** | **官方控制台入口识别**（v1.2.0 新增）：`_safe_mtime`(513) / `_read_head`(520) / `detect_console`(529) |
| 585–614 | `snapshot_payload()`(585) —— `/api/data` 的数据源 |
| 616–691 | 设置持久化：`settings_path`(635) / `_deep_merge`(640) / `load_settings`(650) / `save_settings`(663) / `_local_day_start`(674) / `_as_int`(679) / `_as_float`(686) |
| 693–868 | 提醒引擎 `evaluate_alerts`(693) 与实时面板数据 `live_payload`(811) |
| 870–987 | 控制台用量明细 CSV 导入：`_decode_text`(884) / `_match_headers`(893) / `score_csv`(907) / `scan_csv_candidates`(951) / `safe_csv_path`(972，路径越界防护) |
| 989–1111 | 自检：`diagnose`(1001) / `print_doctor`(1033) |
| 1113–1267 | 系统托盘：`tray_available`(1118) / `_tray_image`(1127) / `_http_json`(1140) / `tray_needs_main_thread`(1153) / `start_tray`(1163) |
| 1269–1404 | HTTP：`Handler`(1277，路由 + gzip + 2s 缓存) |
| 1406–1463 | `serve()`(1406) —— 启动服务与托盘编排 |
| 1465–1516 | `main()`(1465) —— CLI 参数与启动前端口探测 |

---

## 2. 运行方式

### 方式一：用 exe（不装 Python）

- 下载：`https://github.com/dengjihui1/trae-usage-monitor/releases/latest/download/trae-usage-monitor.exe`
- 双击运行。**GUI 子系统（无控制台黑框）**，静默常驻系统托盘，浏览器自动打开看板
- 退出：右键托盘图标 →「退出监控」
- 重复双击：会弹窗提示「端口已被占用」，不会抢端口、不会崩

### 方式二：用 Python 跑

```bash
cd D:\Trae\trae-usage-monitor
python trae_usage_monitor.py
```

Windows 双击 `start.bat`；macOS 双击 `start.command`。
系统托盘是**可选增强**：`pip install pystray pillow`，没装就静默降级为「只有页面内实时面板」。

### 方式三：自己构建

```bash
build.bat      # Windows  -> dist\trae-usage-monitor.exe
./build.sh     # macOS/Linux -> dist/trae-usage-monitor
```

### 完整命令行参数

```bash
python trae_usage_monitor.py --port 9000          # 换端口（默认 8788）
python trae_usage_monitor.py --no-open            # 不自动开浏览器
python trae_usage_monitor.py --no-tray            # 不启用托盘
python trae_usage_monitor.py --json               # 打印解析结果 JSON 后退出
python trae_usage_monitor.py --out data.json      # 导出快照
python trae_usage_monitor.py --doctor             # 自检：日志格式 + 资源是否正常
python trae_usage_monitor.py --log-root "D:\logs" # 手动指定日志根目录
```

---

## 3. HTTP 接口清单

服务只绑定 `127.0.0.1`。所有响应 `Content-Type: application/json; charset=utf-8`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/data` | 全量数据（轮次 / 会话 / 未归属响应 / `official` 官方入口 / 文件元信息）。服务端 2s 内存缓存 |
| GET | `/api/live` | 秒级精简数据（今日轮次 / 活跃会话 / 最近模型 / 成功率 / 24 轮流水 / 提醒列表），托盘也轮询它 |
| GET | `/api/settings` | 读提醒设置；`?defaults=1` 返回默认值 |
| POST | `/api/settings` | 深度合并写入设置 |
| GET | `/api/csv/candidates` | 扫描下载目录里疑似「用量明细」的 CSV（只读每个文件前 64KB 判断表头） |
| GET | `/api/csv/read` | 读取指定 CSV 全文（**路径必须在下载目录内**，越界直接拒绝） |
| GET | `/` `/app.js` `/app.css` `/favicon.ico` | 静态资源（白名单 dict；>512B 才 gzip，ico 不压） |

轮询分层：`/api/live` 默认 2s、`/api/data` 默认 30s（可在设置里改 `pollSeconds` / `dashboardSeconds`）。

`/api/data` 返回体里的 `official` 键结构（前端「查看官方用量」按钮的跳转目标）：

```json
{
  "official": {
    "consoleUrl": "https://console.enterprise.trae.cn",
    "edition": "enterprise",
    "region": "cn",
    "source": "log"
  }
}
```

`edition` ∈ `enterprise` / `personal` / `unknown`；`region` ∈ `cn` / `intl`；
`source` ∈ `log`（从日志里认出来的真实控制台地址）/ `guess`（没认出来，按区域退回官网首页）。

---

## 4. 数据来源与归因原理

### 日志位置（自动探测，按优先级）

| 平台 | 路径 |
| --- | --- |
| Windows | `%APPDATA%\Trae CN\logs\<启动时间戳>\window<n>\renderer*.log` |
| macOS | `~/Library/Application Support/Trae CN/logs/...` |
| Linux | `~/.config/Trae CN/logs/...` |

找不到时在同级目录扫 `Trae*` 再找一遍（`_APP_DIRS = ("Trae CN", "Trae", "Trae Solo")`）。

### 用到的四类日志事件

| 常量 | 事件字符串 | 作用 | 关键字段 |
| --- | --- | --- | --- |
| `EV_SNAPSHOT` | `created chat request selection snapshot ` | 一轮请求的锚点 | sessionId, messageId, agentId, agentName, modelName, customModelProvider |
| `EV_AGENTINFO` | `created empty user message selection snapshot ` | 补会话信息 | sessionId, agentName |
| `EV_MODE` | `model auto selection resolved ` | 该轮是 auto 还是手选 | sessionId, agentId, strategy(auto/manual), source |
| `EV_REPORT` | `reportFrontResponse, status: ` | 该轮结果回执 | model, costTime, errCode, traceId |

> 日志格式是 Trae 的内部实现，**升级后可能变**。所以格式变化时只需要改上面四个常量，
> 并且解析器对不认识的行**永远静默跳过**，最差情况是统计变少，不会崩。

### 归因规则（`build_turns`）

1. 每个 `snapshot` 按 `sessionId + agentId` 找**时间最近**的 `mode` 事件，判定该轮是 auto 还是 manual。
2. `reportFrontResponse` **不含 sessionId**，所以先把模型名归一化
   （`custom_responses_compatible//gpt-6.1-sol` → `gpt-6.1-sol`），再与最近的、尚未匹配的 snapshot 对齐。
3. 对不上的回执单独进 `unmatchedReports`（「未归属响应」），**宁可不猜也不给错的数据**。

### 官方控制台入口识别（`detect_console`，v1.2.0 新增）

额度与余额的数据只有**官方控制台**有，本地日志里没有（也不该去猜）。所以本项目不查额度，
只提供「查看官方用量」按钮一键跳转 —— 但 **个人版 / 企业版 / 国际版是三套不同域名**，
写死一个会让别的用户点开 404。所以由后端从日志里认出来，三级回退：

1. 日志里出现 `"consoleHost":"https://…"`（Trae 真实配置字段，把它读出来；
   同一份配置会被重复打印，取出现次数最多的那个更稳）→ `source = "log"`
2. 没找到就退一步，扫任意 `https://console*.trae.*` 形式的 URL → `source = "log"`
3. 都没有：按日志里 `trae.ai` / `trae.cn` 的出现次数猜区域，退回官网首页
   （`https://www.trae.ai` / `https://www.trae.cn`）→ `source = "guess"`

结果按 `log_root` 缓存（进程内），避免每次请求都重新扫日志。`--doctor` 会打印识别结果。
**注意跳的是控制台入口，不是用量页深链** —— 深链路径各版本不一致，猜错对别的用户就是 404。

---

## 5. 用量提醒机制（重要口径说明）

**为什么叫「用量提醒」而不是「额度提醒」**：本地日志里**没有额度余额，也没有 token 数值**。
用轮次反推额度会严重误导（不同模型单价差几十倍，100 轮 Auto 和 100 轮手选贵模型完全不是一回事）。
所以只提醒本地真实可见的指标，不接任何云 API、不用密钥。

提醒是**状态型**语义（条件成立就返回，前端去重），不是事件型。

### 默认规则（`DEFAULT_SETTINGS`）

| 设置键 | 默认 | 含义 |
| --- | --- | --- |
| `alerts.enabled` | `true` | 总开关 |
| `alerts.todayTurns` | `enabled=true, threshold=300` | 今日累计轮次超阈 |
| `alerts.sessionTurns` | `enabled=true, threshold=80` | 单会话今日轮次超阈 |
| `alerts.errorRate` | `enabled=true, threshold=20, minSamples=5` | 有明确回执的轮次里失败占比超阈 |
| `alerts.avgCost` | `enabled=false, threshold=60000, minSamples=5` | 平均耗时超阈（毫秒） |
| `alerts.autoSwitch` | `enabled=false, model=""` | Auto 路由落到指定模型 |
| `pollSeconds` / `dashboardSeconds` | `2` / `30` | 轮询间隔 |
| `notify.browser` / `notify.tray` | `false` / `true` | 通知方式 |

设置文件：`~/.trae-usage-monitor/settings.json`（Windows 为 `C:\Users\<你>\.trae-usage-monitor\settings.json`），纯文本可读可删。

---

## 6. 关键工程决策与踩过的坑

### 6.1 零依赖是刻意的
解析、归因、HTTP、前端全部只用标准库 + 原生 Web 技术，没有 pip 依赖、无供应链风险。
托盘是唯一可选增强（`pystray` + `Pillow`），缺失时静默降级。

### 6.2 PyInstaller 打包
- 必须加 `--exclude-module numpy --exclude-module psutil`：PIL 的 PyInstaller hook 会连带拉进 numpy，光它一个就 +15MB。优化后 **26.9MB → 16.2MB**。
- exe 用 `--noconsole`（GUI 子系统，PE subsystem = 2），双击不弹黑框。

### 6.3 `--noconsole` 带来的三个必修护栏
1. **`_stderr()` 安全写 stderr** —— `BaseHTTPRequestHandler.log_message` 在 `send_response()` **之前**就被调用，而 windowed 打包下 `sys.stderr` 是 `None`，一旦这里抛异常**整个请求都会废掉、看板直接打不开**。
2. **`_ensure_console()`** —— CLI 模式（`--doctor`/`--json`/`--out`）没有控制台就输出不出来，用 `AllocConsole()` + `open("CONOUT$","w",encoding="utf-8")` 现补一个，**并且必须 `SetConsoleOutputCP(65001)` + `SetConsoleCP(65001)`**，否则新控制台默认 936 代码页会把中文写成乱码。
3. **`_report_fatal()`** —— 现补的控制台会随进程退出一起消失（用户只看到黑框一闪），所以致命错误必须弹 `MessageBoxW`（错误用 `MB_ICONERROR=0x10`）。

### 6.4 Windows 端口劫持坑（真实修过的 bug）
`http.server` 默认 `allow_reuse_address = 1`，在 Windows 上 `SO_REUSEADDR` 会**放行第二个进程绑到同一端口并抢走流量** ——
表现为两个实例都活着、只有后启动的在服务、出现两个托盘图标。
所以启动前必须自己 `_port_in_use()`（`connect_ex` 到 `127.0.0.1:port`）探测，**不能指望 bind 报错**。

### 6.5 托盘线程模型（跨平台）
macOS 的 AppKit / Linux 的 GTK 硬性要求事件循环在**主线程**，所以 `tray_needs_main_thread()` 返回 `sys.platform != "win32"`：
- mac/Linux：HTTP 放后台线程，主线程交给托盘
- Windows：主线程跑 `serve_forever()` 以保住 Ctrl+C 即时响应

### 6.6 其他
- 托盘未启用（`--no-tray` 或依赖缺失）时也要补控制台，否则变成既没托盘也没控制台、只能去任务管理器强杀的静默进程。
- 静态资源白名单用 dict 而非 tuple，以便按扩展名给 Content-Type 并跳过 ico 的 gzip。
- 换行符：`.gitattributes` 固定 `*.sh`/`*.command` 为 LF（否则 shebang 混入 `\r`，mac/Linux 无法执行），`*.bat` 为 CRLF。

---

## 7. 版本历史

| 版本 | 内容 |
| --- | --- |
| v1.0.0 | Auto 模型逐轮归因看板（核心） |
| v1.1.0 | 秒级实时用量面板 + 5 条可调阈值用量提醒 + 可选系统托盘；exe 体积 26.9MB → 16.2MB |
| v1.1.1 | 补齐 macOS / Linux 支持（托盘线程模型 + `build.sh` / `start.command`） |
| v1.1.2 | **Windows 落地优化**：`--noconsole` 静默无窗口 + 自带图标；修掉端口被抢、补控制台中文乱码、启动失败无提示三个缺陷 |
| **v1.2.0** | **「查看官方用量」一键跳转**：从日志识别个人版 / 企业版 / 国际版控制台域名（三级回退），不再写死；`--doctor` 同步输出识别结果；新增 `PROJECT.md` |

- 各版本 exe 大小：v1.1.1 = 16,189,327 B；v1.1.2 = 16,181,327 B；**v1.2.0 = 16,184,068 B**
- git 提交：`a088740`(v1.0.0) → `a7862f2` → `6e661d0`(v1.1) → `32e6497`(v1.1.1) → `99eb524`(v1.1.2) → **`50e9984`(v1.2.0，当前 HEAD)**

### 发布产物（GitHub Releases）

| 版本 | Release 页面 | exe 直链 |
| --- | --- | --- |
| v1.2.0 | [releases/tag/v1.2.0](https://github.com/dengjihui1/trae-usage-monitor/releases/tag/v1.2.0) | [trae-usage-monitor.exe](https://github.com/dengjihui1/trae-usage-monitor/releases/download/v1.2.0/trae-usage-monitor.exe) |
| 永远指向最新 | [releases/latest](https://github.com/dengjihui1/trae-usage-monitor/releases/latest) | [latest/download/trae-usage-monitor.exe](https://github.com/dengjihui1/trae-usage-monitor/releases/latest/download/trae-usage-monitor.exe) |

v1.2.0 的 exe 校验和（下载后可核对）：

```
sha256  5d541c4aed82c4c4afc04aa06042291874acca52301d5c86daf60d7b48de4785
size    16184068 bytes
```

> 每个版本都打了**附注标签**（`git tag -a v1.x.y`），标签对象与提交分开，
> 所以 `git ls-remote --tags` 看到的 `v1.1.2` 提交号与 `v1.1.2^{}` 不是同一个，属正常。

---

## 8. 已完成的验证 vs 未验证的边界

### 已用**实际发布的 exe** 端到端实测通过（Windows）

- PE 子系统 = 2（GUI）→ 确认不弹控制台
- 图标资源已嵌入 `.rsrc`
- `/`、`/app.js`、`/app.css`、`/favicon.ico`、`/api/live`、`/api/data` 全部 200
- `--doctor` 退出码 0，中文输出正确
- 重复双击：第一实例独占端口，第二个堵在弹窗上等确认
- `--no-tray`：自动补控制台，服务正常
- **v1.2.0 官方入口识别**：本机识别为 `https://console.enterprise.trae.cn（enterprise / cn，日志识别）`；
  `/api/data` 返回 `official` 字段正确；回退路径（空日志目录）输出 `https://www.trae.cn（unknown / cn，未识别，退回官网首页）`
- **v1.2.0 发布产物**：已发布 Release 并上传 `trae-usage-monitor.exe`（16,184,068 B），
  匿名访问 Release API 复核 `draft=false`、附件体积与本地一致

### 未验证（诚实边界）

- **macOS / Linux 代码路径从未在真机跑过**（开发机是 Windows），日志路径按 VS Code 惯例推断，托盘只能靠源码分析判断。README 已标注。
- 两处纯视觉项无法用脚本确认：托盘图标实际渲染效果、资源管理器里 exe 图标的显示。
- 日志格式是 Trae 内部实现，未来版本可能变（`--doctor` 就是为此准备的）。

### 明确不做

- **查额度**（本地无余额 / token 数据，用轮次反推会严重误导）—— 改为提供「查看官方用量」跳转按钮，额度以官方控制台为准
- 读取对话正文（只取含事件标识的行及其后的 JSON 元数据）
- 任何联网行为（无遥测、无上传；「查看官方用量」只是浏览器打开官方页面，本工具自身不请求任何外部接口）

---

## 9. 开发约定

- **提交身份必须内联**（本机 git 全局身份未配）：
  ```bash
  git -c user.name="Zihui Deng" -c user.email="dengjihui1@users.noreply.github.com" commit -m "..."
  ```
- **发 Release**：本机**没有 gh CLI**，走 GitHub REST API；token 从 `git credential fill`（host=github.com）非交互获取（`GIT_TERMINAL_PROMPT=0`），**禁止打印 token 明文**。
  - 注意：PowerShell 的 `ConvertTo-Json` 会破坏中文多行 body，要先写成 UTF-8 文件再提交字节。
  - 上传附件时如遇「file is being used by another process」：`--onefile` 的父进程被杀后**子进程仍存活**，要按路径找全再杀。
- **改 `build.bat` 后**必须把全文规范回 CRLF 并断言纯 ASCII（Edit 工具会写成裸 LF）。
- 代码注释、文档、对话一律中文；`README.en.md` 与 `build.bat` 内注释用英文。

---

## 10. 如果要继续改，最可能动的地方

| 想改什么 | 改哪里 |
| --- | --- |
| Trae 升级后解析不到 | `trae_usage_monitor.py` 顶部四个 `EV_*` 常量（先跑 `--doctor` 看命中数） |
| 归因不准 | `build_turns()`（349 行） |
| 官方入口认错 / 想改成深链 | `detect_console()`（529 行）+ 顶部 `_CONSOLE_HOST_RE` / `_OFFICIAL_SITE`（505 / 507 行） |
| 提醒规则 / 阈值默认值 | `DEFAULT_SETTINGS`（620 行）+ `evaluate_alerts()`（693 行） |
| 前端界面 | `web/index.html` + `web/app.js` + `web/app.css`（无构建，改完刷新即可） |
| 打包参数 | `build.bat` / `build.sh` |
