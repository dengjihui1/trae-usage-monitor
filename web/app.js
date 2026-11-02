'use strict';

const PALETTE = ['#4da3ff', '#33d2a1', '#ffb648', '#c084fc', '#ff8a5c', '#5ee7df', '#f472b6', '#94a3b8'];
const MAX_ROWS = 400;

const state = {
  data: null,
  days: 30,
  sessionFilter: '',
  modelFilter: '',
  modeFilter: '',
  csv: null,
  csvSource: null,
  timer: null,
};

const $ = (id) => document.getElementById(id);

// ---------- 会话命名 ----------
// 本地日志不含会话标题（标题只在云端），所以默认用 Agent 名，并允许用户自己起名。
const NAME_KEY = 'trae-um.sessionNames';
function loadNames() {
  try {
    return JSON.parse(localStorage.getItem(NAME_KEY) || '{}') || {};
  } catch {
    return {};
  }
}
function saveNames(map) {
  localStorage.setItem(NAME_KEY, JSON.stringify(map));
}
let customNames = loadNames();
let sessionMap = new Map();

function agentDisplay(s) {
  if (!s) return 'Agent';
  if (s.agentName && s.agentName !== 'Agent') return s.agentName;
  if (s.agentType === 'solo_agent') return 'Trae Agent';
  return s.agentName || s.agentType || 'Agent';
}

function sessionLabel(sid) {
  return customNames[sid] || agentDisplay(sessionMap.get(sid)) || sid.slice(0, 10);
}

function sessionShort(sid) {
  return sid ? sid.slice(0, 8) : '';
}

function modelColor(name, indexMap) {
  if (!indexMap.has(name)) indexMap.set(name, indexMap.size % PALETTE.length);
  return PALETTE[indexMap.get(name)];
}

function fmtDur(ms) {
  if (ms === null || ms === undefined || ms === '') return '—';
  const n = Number(ms);
  if (!isFinite(n)) return '—';
  if (n < 1000) return `${n} ms`;
  if (n < 60000) return `${(n / 1000).toFixed(1)} s`;
  if (n < 3600000) return `${Math.round(n / 60000)} min`;
  return `${(n / 3600000).toFixed(1)} h`;
}

function fmtTime(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function dayKey(iso) {
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function statusClass(status) {
  if (!status || status === 'Unknown') return 'pending';
  return status === 'Success' ? 'ok' : 'fail';
}

// ---------- 加载 ----------

async function load() {
  const res = await fetch(`/api/data?days=${state.days}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`API ${res.status}`);
  state.data = await res.json();
  applyOfficial(state.data.official);
  render();
}

async function refresh() {
  try {
    await load();
  } catch (err) {
    $('meta').innerHTML = `<span style="color:#ff6b6b">加载失败：${esc(err.message)}</span>`;
  }
}

// 「查看官方用量」入口。额度与余额只有官方控制台有，这里只统计本地日志看得见的用量。
// 具体跳哪个域名由后端从日志里认出来 —— 个人版 / 国际版 / 企业版是三套不同域名，
// 写死一个会让别的用户点开 404。
function applyOfficial(official) {
  const a = $('officialUsage');
  if (!a) return;
  const url = (official && official.consoleUrl) || '';
  if (!url) {
    a.hidden = true;
    return;
  }
  a.href = url;
  a.hidden = false;
  const where =
    official.edition === 'enterprise' ? '企业版控制台'
    : official.edition === 'personal' ? '官方控制台'
    : '官方站点';
  a.title = `额度与余额以${where}为准；这里只统计本地日志里的用量`;
}

// ---------- 渲染 ----------

function render() {
  const d = state.data;
  if (!d) return;
  sessionMap = new Map((d.sessions || []).map((s) => [s.sessionId, s]));
  colorIndex = new Map();
  renderMeta(d);
  renderKpis(d);
  renderTrace(d);
  renderModelBars(d);
  renderMode(d);
  renderStatus(d);
  renderDaily(d);
  renderSessions(d);
  renderTurns(d);
  renderCsv();
  if (LIVE.data) renderLive(LIVE.data);
}

function renderMeta(d) {
  const c = d.counts || {};
  const parts = [
    `<span class="tag">日志 <b>${d.files.length}</b> 个文件</span>`,
    `<span class="tag">归因轮次 <b>${c.turns}</b></span>`,
    `<span class="tag">已匹配响应 <b>${c.matched}</b></span>`,
    `<span class="tag">未归属响应 <b>${c.unmatchedReports}</b></span>`,
    `更新于 ${esc(d.generatedAt)} · 目录 ${esc(d.logRoot)}`,
  ];
  if (!d.logRootExists) parts.push('<span style="color:#ff6b6b">日志目录不存在</span>');
  $('meta').innerHTML = parts.join(' ');
}

function renderKpis(d) {
  const turns = d.turns;
  const total = turns.length;
  const auto = turns.filter((t) => t.mode === 'auto').length;
  const models = new Set(turns.map((t) => t.model));
  const ok = turns.filter((t) => t.status === 'Success').length;
  const known = turns.filter((t) => t.status && t.status !== 'Unknown').length;
  const durations = turns.map((t) => Number(t.costTime)).filter((n) => isFinite(n) && n > 0);
  const avg = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;
  const sessions = new Set(turns.map((t) => t.sessionId)).size;

  const cards = [
    { label: '对话轮次', value: total, unit: '轮' },
    { label: 'Auto 自动选模型', value: total ? ((auto / total) * 100).toFixed(0) : '0', unit: '%', sub: `${auto} / ${total}` },
    { label: '覆盖模型', value: models.size, unit: '个' },
    { label: '成功响应', value: known ? ((ok / known) * 100).toFixed(0) : '—', unit: known ? '%' : '', sub: known ? `${ok} / ${known}` : '无数据' },
    { label: '平均耗时', value: avg !== null ? fmtDur(avg).split(' ')[0] : '—', unit: avg !== null ? fmtDur(avg).split(' ')[1] : '' },
    { label: '会话数', value: sessions, unit: '个' },
  ];

  $('kpis').innerHTML = cards
    .map(
      (c) => `<div class="kpi"><div class="label">${esc(c.label)}</div>
      <div class="value">${esc(c.value)}${c.unit ? `<small>${esc(c.unit)}</small>` : ''}</div>
      ${c.sub ? `<div class="hint" style="margin-top:4px">${esc(c.sub)}</div>` : ''}</div>`
    )
    .join('');
}

function groupCount(turns, keyFn) {
  const map = new Map();
  for (const t of turns) {
    const k = keyFn(t);
    const cur = map.get(k) || { count: 0, sum: 0, ok: 0, known: 0 };
    cur.count += 1;
    const n = Number(t.costTime);
    if (isFinite(n) && n > 0) cur.sum += n;
    if (t.status && t.status !== 'Unknown') {
      cur.known += 1;
      if (t.status === 'Success') cur.ok += 1;
    }
    map.set(k, cur);
  }
  return map;
}

let colorIndex = new Map();

function renderTrace(d) {
  const showManual = $('traceManual').checked;
  const groups = new Map();
  const ordered = [...d.turns].sort((a, b) => a.ts - b.ts);
  for (const t of ordered) {
    if (!showManual && t.mode !== 'auto') continue;
    if (!groups.has(t.sessionId)) groups.set(t.sessionId, []);
    groups.get(t.sessionId).push(t);
  }
  const rows = [...groups.entries()];
  if (!rows.length) {
    $('trace').innerHTML = '<div class="empty">这个时间范围内没有可显示的轮次</div>';
    return;
  }
  rows.sort((a, b) => b[1][b[1].length - 1].ts - a[1][a[1].length - 1].ts);

  const CAP = 40;
  $('trace').innerHTML = rows
    .map(([sid, list]) => {
      const autoCount = list.filter((t) => t.mode === 'auto').length;
      const uniq = new Set(list.map((t) => t.model));
      let prev = null;
      const chips = list.slice(0, CAP).map((t) => {
        const color = modelColor(t.model, colorIndex);
        const switched = t.mode === 'auto' && prev && prev.mode === 'auto' && prev.model !== t.model;
        const tip = `${fmtTime(t.time)} · ${t.model} · ${t.mode} · ${fmtDur(t.costTime)} · ${t.status}`;
        prev = t;
        return `<span class="chip ${t.mode === 'manual' ? 'manual' : ''} ${switched ? 'switched' : ''}" style="--c:${color}" title="${esc(tip)}"><i class="sw"></i>${esc(t.model)}</span>`;
      });
      const extra = list.length > CAP ? `<span class="more">+${list.length - CAP} 轮</span>` : '';
      const active = state.sessionFilter === sid ? ' active' : '';
      return `<div class="trace-row${active}" data-sid="${esc(sid)}">
        <div class="trace-head">
          <div class="tname" title="${esc(sid)}">${esc(sessionLabel(sid))}</div>
          <div class="tmeta">${esc(sessionShort(sid))} · auto ${autoCount}/${list.length} 轮 · ${uniq.size} 个模型</div>
        </div>
        <div class="trace-flow">${chips.join('<span class="arrow">→</span>')}${extra}</div>
      </div>`;
    })
    .join('');

  $('trace').querySelectorAll('.trace-row').forEach((row) => {
    row.addEventListener('click', () => {
      const sid = row.dataset.sid;
      state.sessionFilter = state.sessionFilter === sid ? '' : sid;
      renderSessions(state.data);
      renderTurns(state.data);
      renderTrace(state.data);
    });
  });
}

function renderModelBars(d) {
  const turns = d.turns;
  const map = groupCount(turns, (t) => t.model || '(未知)');
  const rows = [...map.entries()].sort((a, b) => b[1].count - a[1].count);
  const max = rows.length ? rows[0][1].count : 1;
  if (!rows.length) {
    $('modelBars').innerHTML = '<div class="empty">还没有可归因的对话轮次</div>';
    return;
  }
  $('modelBars').innerHTML = rows
    .map(([name, s]) => {
      const color = modelColor(name, colorIndex);
      const share = turns.length ? ((s.count / turns.length) * 100).toFixed(0) : 0;
      const rate = s.known ? `${Math.round((s.ok / s.known) * 100)}%` : '—';
      return `<div class="bar-row" title="${esc(name)} · 成功率 ${rate} · 平均耗时 ${fmtDur(s.sum / (s.count || 1))}">
        <div class="name">${esc(name)}</div>
        <div class="track"><div class="fill" style="width:${(s.count / max) * 100}%;background:${color}"></div></div>
        <div class="nums"><b>${s.count}</b> 轮 · ${share}%</div>
      </div>`;
    })
    .join('');
}

function renderMode(d) {
  const turns = d.turns;
  const total = turns.length || 1;
  const auto = turns.filter((t) => t.mode === 'auto').length;
  const manual = turns.filter((t) => t.mode === 'manual').length;
  const unknown = total ? turns.length - auto - manual : 0;
  const a = (auto / total) * 100;
  const m = (manual / total) * 100;
  const u = Math.max(0, 100 - a - m);
  const donut = $('donut');
  donut.style.background = `conic-gradient(#4da3ff 0 ${a}%, #ffb648 ${a}% ${a + m}%, #3a4757 ${a + m}% ${a + m + u}%)`;
  donut.innerHTML = `<div class="inner"><div><b>${total ? Math.round(a) : 0}%</b>auto</div></div>`;
  $('legend').innerHTML = [
    ['#4da3ff', 'auto 自动路由', auto],
    ['#ffb648', 'manual 手选', manual],
    ['#3a4757', 'unknown', unknown],
  ]
    .map(([c, label, v]) => `<div><i style="background:${c}"></i>${esc(label)} <b style="color:#e6edf6">${v}</b></div>`)
    .join('');
}

function renderStatus(d) {
  const map = groupCount(d.turns, (t) => t.status || 'Unknown');
  const rows = [...map.entries()].sort((a, b) => b[1].count - a[1].count);
  const max = rows.length ? rows[0][1].count : 1;
  const colors = { Success: '#33d2a1', Unknown: '#3a4757' };
  $('statusBars').innerHTML = rows
    .map(([name, s]) => {
      const color = colors[name] || '#ff6b6b';
      return `<div class="bar-row">
        <div class="name">${esc(name)}</div>
        <div class="track"><div class="fill" style="width:${(s.count / max) * 100}%;background:${color}"></div></div>
        <div class="nums"><b>${s.count}</b></div>
      </div>`;
    })
    .join('');
}

function renderDaily(d) {
  const models = [...new Set(d.turns.map((t) => t.model))].sort();
  const idx = new Map();
  models.forEach((m, i) => idx.set(m, PALETTE[i % PALETTE.length]));

  const days = new Map();
  for (const t of d.turns) {
    const k = dayKey(t.time);
    if (!days.has(k)) days.set(k, new Map());
    const bucket = days.get(k);
    bucket.set(t.model, (bucket.get(t.model) || 0) + 1);
  }
  const keys = [...days.keys()].sort().slice(-16);
  if (!keys.length) {
    $('daily').innerHTML = '<div class="empty">暂无数据</div>';
    $('dailyLegend').innerHTML = '';
    return;
  }
  const totals = keys.map((k) => [...days.get(k).values()].reduce((a, b) => a + b, 0));
  const max = Math.max(...totals, 1);
  const H = 150;

  $('daily').innerHTML = keys
    .map((k, i) => {
      const bucket = days.get(k);
      const segs = models
        .filter((m) => bucket.has(m))
        .map((m) => `<div class="seg" style="height:${(bucket.get(m) / max) * H}px;background:${idx.get(m)}" title="${esc(m)}: ${bucket.get(m)}"></div>`)
        .join('');
      return `<div class="col"><div class="stack" style="height:${(totals[i] / max) * H}px" title="${k}: ${totals[i]} 轮">${segs}</div>
        <div class="dlabel">${esc(k.slice(5))}</div></div>`;
    })
    .join('');

  const used = models.filter((m) => keys.some((k) => days.get(k).has(m)));
  $('dailyLegend').innerHTML = used
    .map((m) => `<span><i style="background:${idx.get(m)}"></i>${esc(m)}</span>`)
    .join('');
}

function renderSessions(d) {
  const map = new Map();
  for (const t of d.turns) {
    if (!map.has(t.sessionId)) map.set(t.sessionId, { turns: [], last: t.ts });
    const s = map.get(t.sessionId);
    s.turns.push(t);
    s.last = Math.max(s.last, t.ts);
  }
  const rows = [...map.entries()].sort((a, b) => b[1].last - a[1].last);
  const tbody = $('sessionTable').querySelector('tbody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">暂无会话</td></tr>';
    return;
  }
  tbody.innerHTML = rows
    .map(([sid, s]) => {
      const models = [...new Set(s.turns.map((t) => t.model))];
      const auto = s.turns.filter((t) => t.mode === 'auto').length;
      const selected = state.sessionFilter === sid ? ' selected' : '';
      const agent = agentDisplay(sessionMap.get(sid));
      return `<tr class="clickable${selected}" data-sid="${esc(sid)}">
        <td>
          <div class="sname">
            <span class="txt" title="${esc(sid)}">${esc(sessionLabel(sid))}</span>
            <span class="sid">${esc(sessionShort(sid))}</span>
            <button class="rename-btn" data-rename="${esc(sid)}" title="改名">✎</button>
          </div>
        </td>
        <td class="hint">${esc(agent)}</td>
        <td class="num">${s.turns.length}</td>
        <td>${models.map((m) => `<span class="pill ${m.toLowerCase().includes('gpt') ? 'manual' : 'auto'}">${esc(m)}</span>`).join(' ')}</td>
        <td class="num">${Math.round((auto / s.turns.length) * 100)}%</td>
        <td class="mono">${esc(fmtTime(new Date(s.last * 1000).toISOString()))}</td>
      </tr>`;
    })
    .join('');

  tbody.querySelectorAll('tr[data-sid]').forEach((tr) => {
    tr.addEventListener('click', () => {
      const sid = tr.dataset.sid;
      state.sessionFilter = state.sessionFilter === sid ? '' : sid;
      renderSessions(state.data);
      renderTurns(state.data);
      renderTrace(state.data);
    });
  });

  tbody.querySelectorAll('[data-rename]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const sid = btn.dataset.rename;
      const cur = customNames[sid] || '';
      const v = prompt('给这个会话起个名字（留空则恢复默认，默认 = Agent 名）', cur);
      if (v === null) return;
      if (v.trim()) customNames[sid] = v.trim();
      else delete customNames[sid];
      saveNames(customNames);
      render();
    });
  });
}

function renderTurns(d) {
  const models = [...new Set(d.turns.map((t) => t.model))].sort();
  const sel = $('modelFilter');
  const cur = sel.value;
  sel.innerHTML = '<option value="">全部模型</option>' + models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
  sel.value = cur;

  let rows = d.turns;
  if (state.sessionFilter) rows = rows.filter((t) => t.sessionId === state.sessionFilter);
  if (state.modelFilter) rows = rows.filter((t) => t.model === state.modelFilter);
  if (state.modeFilter) rows = rows.filter((t) => t.mode === state.modeFilter);

  $('filterHint').textContent = state.sessionFilter
    ? `${sessionLabel(state.sessionFilter)} · ${rows.length} 轮`
    : `全部 ${rows.length} 轮`;

  const tbody = $('turnTable').querySelector('tbody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">没有符合条件的轮次</td></tr>';
    return;
  }
  const shown = rows.slice(0, MAX_ROWS);
  tbody.innerHTML = shown
    .map(
      (t) => `<tr>
      <td class="mono">${esc(fmtTime(t.time))}</td>
      <td title="${esc(sessionLabel(t.sessionId))} · ${esc(t.sessionId)}"><span>${esc(sessionLabel(t.sessionId))}</span> <span class="sid">${esc(sessionShort(t.sessionId))}</span></td>
      <td>${esc(t.model)}</td>
      <td><span class="pill ${esc(t.mode)}">${esc(t.mode)}</span></td>
      <td><span class="pill ${statusClass(t.status)}" title="${esc(t.errorMsg || '')}">${esc(t.status)}</span>${t.errCode ? ` <span class="hint">${esc(t.errCode)}</span>` : ''}</td>
      <td class="num">${esc(fmtDur(t.costTime))}</td>
      <td class="hint" title="${esc(t.agentId)}">${esc(t.agentName || t.agentType || '—')}</td>
    </tr>`
    )
    .join('');
  if (rows.length > shown.length) {
    tbody.innerHTML += `<tr><td colspan="7" class="empty">仅显示前 ${shown.length} 条，共 ${rows.length} 条</td></tr>`;
  }
}

// ---------- CSV：补齐 token ----------

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++; } else { quoted = false; }
      } else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(cell); cell = ''; }
    else if (c === '\n') { row.push(cell); rows.push(row); row = []; cell = ''; }
    else if (c === '\r') { /* skip */ }
    else cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows.filter((r) => r.length && r.some((x) => x.trim()));
}

function pickColumn(headers, keywords) {
  for (let i = 0; i < headers.length; i++) {
    const h = headers[i].toLowerCase();
    if (keywords.some((k) => h.includes(k))) return i;
  }
  return -1;
}

function importCsv(text) {
  const rows = parseCsv(text);
  if (rows.length < 2) throw new Error('CSV 内容不足');
  const headers = rows[0].map((h) => h.trim());
  const cols = {
    session: pickColumn(headers, ['session', '会话']),
    model: pickColumn(headers, ['model', '模型']),
    token: pickColumn(headers, ['token', 'tokens', '消耗', '用量']),
    calls: pickColumn(headers, ['调用', '次数', 'count']),
  };
  if (cols.model < 0) throw new Error('未识别到「模型」列');

  const agg = new Map();
  let bad = 0;
  for (const r of rows.slice(1)) {
    const model = r[cols.model] ? r[cols.model].trim() : '';
    if (!model) { bad++; continue; }
    const key = model.toLowerCase().split('//').pop();
    const tokens = cols.token >= 0 ? Number(String(r[cols.token]).replace(/[^\d.]/g, '')) : 0;
    const calls = cols.calls >= 0 ? Number(String(r[cols.calls]).replace(/[^\d.]/g, '')) : 0;
    const cur = agg.get(key) || { model, tokens: 0, calls: 0 };
    if (isFinite(tokens)) cur.tokens += tokens;
    if (isFinite(calls)) cur.calls += calls;
    agg.set(key, cur);
  }
  state.csv = { headers, cols, agg, total: rows.length - 1, bad };
}

function renderCsv() {
  const info = $('csvInfo');
  const host = $('tokenTable');
  if (!state.csv) {
    info.textContent = '未导入 CSV';
    host.innerHTML = '<div class="empty">导入后这里会用控制台的精确 token 补齐每个模型的用量</div>';
    return;
  }
  const { cols, agg, total, bad } = state.csv;
  const src = state.csvSource ? ` · 来源 ${state.csvSource}` : '';
  info.textContent = `已导入 ${total} 行（失败 ${bad}）${src} · 列：模型=${cols.model >= 0 ? '✓' : '✗'} token=${cols.token >= 0 ? '✓' : '✗'} 调用次数=${cols.calls >= 0 ? '✓' : '✗'}`;

  const local = new Map();
  for (const t of state.data.turns) {
    const k = (t.model || '').toLowerCase().split('//').pop();
    local.set(k, (local.get(k) || 0) + 1);
  }
  const keys = [...new Set([...agg.keys(), ...local.keys()])].sort(
    (a, b) => (agg.get(b)?.tokens || 0) - (agg.get(a)?.tokens || 0) || (local.get(b) || 0) - (local.get(a) || 0)
  );

  const rows = keys.map((k) => `<tr>
    <td>${esc(agg.get(k)?.model || k)}</td>
    <td class="num">${local.get(k) || 0}</td>
    <td class="num">${(agg.get(k)?.calls || 0).toLocaleString()}</td>
    <td class="num">${(agg.get(k)?.tokens || 0).toLocaleString()}</td>
  </tr>`);

  const sumTokens = [...agg.values()].reduce((a, b) => a + b.tokens, 0);
  host.innerHTML = `<table>
    <thead><tr><th>模型</th><th>本地轮次</th><th>控制台调用次数</th><th>Token 消耗</th></tr></thead>
    <tbody>${rows.join('')}</tbody>
    <tfoot><tr><th>合计</th><th class="num">${[...local.values()].reduce((a, b) => a + b, 0)}</th>
      <th class="num">${[...agg.values()].reduce((a, b) => a + b.calls, 0).toLocaleString()}</th>
      <th class="num">${sumTokens.toLocaleString()}</th></tr></tfoot>
  </table>`;
}

// ---------- 自动查找下载目录里的用量明细 CSV ----------

const COL_LABEL = { model: '模型', token: 'token', calls: '调用次数', session: '会话' };

async function loadCsvFromServer(path) {
  const res = await fetch(`/api/csv/read?path=${encodeURIComponent(path)}`, { cache: 'no-store' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `API ${res.status}`);
  importCsv(data.text);
  state.csvSource = data.name;
  renderCsv();
}

function renderCandidates(cands, chosen) {
  const host = $('csvCandidates');
  if (!cands.length) {
    host.innerHTML = '';
    return;
  }
  host.innerHTML = cands
    .map((c) => {
      const cols = Object.keys(c.matched || {}).map((k) => COL_LABEL[k] || k).join(' / ');
      const size = Math.max(1, Math.round(c.size / 1024));
      const active = c.path === chosen ? ' active' : '';
      return `<div class="cand${active}">
        <div class="cand-main">
          <div class="cand-name">${esc(c.name)}</div>
          <div class="hint">识别到 ${esc(cols)} · ${size} KB · 匹配度 ${c.score}</div>
        </div>
        <button class="ghost" data-csv="${esc(c.path)}">导入这个</button>
      </div>`;
    })
    .join('');
  host.querySelectorAll('[data-csv]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      try {
        await loadCsvFromServer(btn.dataset.csv);
        renderCandidates(cands, btn.dataset.csv);
        $('csvInfo').textContent = `已导入 ${btn.closest('.cand').querySelector('.cand-name').textContent}`;
      } catch (err) {
        $('csvInfo').textContent = `导入失败：${err.message}`;
      }
    });
  });
}

async function autoFindCsv() {
  const info = $('csvInfo');
  info.textContent = '正在扫描下载目录…';
  $('csvCandidates').innerHTML = '';
  try {
    const res = await fetch('/api/csv/candidates', { cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `API ${res.status}`);
    if (!data.exists) {
      info.textContent = `没找到下载目录（${data.downloads}），请手动选择 CSV。`;
      return;
    }
    if (!data.candidates.length) {
      info.textContent = `下载目录里没有像「用量明细」的 CSV（${data.downloads}），请手动选择。`;
      return;
    }
    const top = data.candidates[0];
    await loadCsvFromServer(top.path);
    renderCandidates(data.candidates, top.path);
    info.textContent = `已自动导入最匹配的「${top.name}」（共 ${data.candidates.length} 个候选，可切换）`;
  } catch (err) {
    info.textContent = `自动查找失败：${err.message}，请手动选择 CSV。`;
  }
}

// ---------- 实时面板 + 用量提醒 ----------

const LIVE = {
  data: null,
  stamp: null,
  settings: null,
  notifiedSig: '',
  timer: null,
  clockTimer: null,
};

function setText(id, v) {
  const el = $(id);
  if (el) el.textContent = v;
}

function liveClock() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  setText('liveClock', `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`);
}

async function fetchLive() {
  try {
    const res = await fetch('/api/live', { cache: 'no-store' });
    if (!res.ok) throw new Error(`API ${res.status}`);
    const d = await res.json();
    const changed = d.stamp !== LIVE.stamp;
    LIVE.data = d;
    LIVE.stamp = d.stamp;
    if (changed) renderLive(d);
    renderAlerts(d.alerts || []);
    applyNotify(d);
    setText('liveHint', `每 ${d.settings ? d.settings.pollSeconds : 2} 秒刷新`);
  } catch (err) {
    setText('liveHint', `实时接口异常：${err.message}`);
  }
}

function renderLive(d) {
  const t = d.today || {};
  setText('lvTodayTurns', t.turns ?? 0);
  setText('lvTodaySub', `Auto ${t.autoTurns ?? 0} 轮 · 历史共 ${d.total ?? 0} 轮`);

  const cur = d.current || {};
  if (cur.sessionId) {
    setText('lvSession', sessionLabel(cur.sessionId));
    setText('lvSessionSub', `${sessionShort(cur.sessionId)} · 今日 ${cur.turns} 轮`);
  } else {
    setText('lvSession', '空闲');
    setText('lvSessionSub', '最近 10 分钟内没有新对话');
  }

  const last = d.last;
  if (last) {
    setText('lvLastModel', last.model || '—');
    setText('lvLastSub', `${last.mode || '未知'} · ${last.status || '未知'} · ${fmtDur(last.costTime)} · ${fmtTime(last.time)}`);
  } else {
    setText('lvLastModel', '—');
    setText('lvLastSub', '还没有归因到轮次');
  }

  if (t.successRate === null || t.successRate === undefined) {
    setText('lvTodayRate', '—');
    setText('lvTodayRateSub', '今日暂无已回执响应');
  } else {
    setText('lvTodayRate', `${Math.round(t.successRate)}%`);
    setText('lvTodayRateSub', `${t.success}/${t.known} 轮 · 平均 ${fmtDur(t.avgCostTime)}`);
  }

  setText('liveHint', `每 ${d.settings ? d.settings.pollSeconds : 2} 秒刷新 · ${d.logRootExists ? '日志正常' : '日志目录不存在'}`);
  renderFlow(d.recent || []);
}

function renderFlow(recent) {
  const host = $('lvFlow');
  if (!recent.length) {
    host.innerHTML = '<span class="empty">暂无轮次</span>';
    return;
  }
  host.innerHTML = [...recent]
    .reverse()
    .map((t, i) => {
      const color = modelColor(t.model, colorIndex);
      const tip = `${fmtTime(t.time)} · ${t.model} · ${t.mode} · ${t.status} · ${fmtDur(t.costTime)}`;
      return `<span class="lchip${i === 0 ? ' fresh' : ''}" style="--c:${color}" title="${esc(tip)}"><i class="st ${statusClass(t.status)}"></i>${esc(t.model || '—')}</span>`;
    })
    .join('');
}

const ALERT_MARK = { warn: '!', bad: '×', info: 'i' };

function renderAlerts(alerts) {
  const host = $('alerts');
  if (!alerts.length) {
    host.innerHTML = '';
    return;
  }
  host.innerHTML = alerts
    .map(
      (a) => `<div class="alert ${esc(a.level || 'warn')}">
      <span class="ai">${ALERT_MARK[a.level] || '!'}</span>
      <b>${esc(a.title || '提醒')}</b>
      <span class="ad">${esc(a.detail || '')}</span>
    </div>`
    )
    .join('');
}

function alertKey(a) {
  return `${a.code}|${a.sessionId || ''}|${a.threshold ?? ''}`;
}

function applyNotify(d) {
  const alerts = d.alerts || [];
  if (!alerts.length) {
    LIVE.notifiedSig = '';
    return;
  }
  const sig = alerts.map(alertKey).join(',');
  if (sig === LIVE.notifiedSig) return;
  if (!(d.settings && d.settings.browserNotify)) return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  LIVE.notifiedSig = sig;
  const a = alerts[0];
  try {
    new Notification(`Trae 用量提醒 · ${a.title}`, { body: a.detail, tag: 'trae-usage-alert' });
  } catch {
    /* 部分浏览器在非安全上下文下会抛异常，忽略 */
  }
}

// ---------- 提醒设置 ----------

const SETTINGS_FIELDS = [
  { p: ['alerts', 'enabled'], t: 'bool', l: '提醒总开关', u: '页面与托盘共用' },
  { p: ['alerts', 'todayTurns', 'enabled'], t: 'bool', l: '今日轮次超阈' },
  { p: ['alerts', 'todayTurns', 'threshold'], t: 'int', l: '今日轮次阈值', u: '轮' },
  { p: ['alerts', 'sessionTurns', 'enabled'], t: 'bool', l: '单会话轮次超阈' },
  { p: ['alerts', 'sessionTurns', 'threshold'], t: 'int', l: '单会话轮次阈值', u: '轮' },
  { p: ['alerts', 'errorRate', 'enabled'], t: 'bool', l: '失败率超阈' },
  { p: ['alerts', 'errorRate', 'threshold'], t: 'int', l: '失败率阈值', u: '%' },
  { p: ['alerts', 'errorRate', 'minSamples'], t: 'int', l: '失败率最小样本', u: '轮' },
  { p: ['alerts', 'avgCost', 'enabled'], t: 'bool', l: '平均耗时超阈' },
  { p: ['alerts', 'avgCost', 'threshold'], t: 'int', l: '平均耗时阈值', u: '毫秒' },
  { p: ['alerts', 'avgCost', 'minSamples'], t: 'int', l: '耗时最小样本', u: '轮' },
  { p: ['alerts', 'autoSwitch', 'enabled'], t: 'bool', l: 'Auto 命中指定模型' },
  { p: ['alerts', 'autoSwitch', 'model'], t: 'text', l: '关注的模型名', u: '可留空' },
  { p: ['pollSeconds'], t: 'float', l: '实时面板刷新间隔', u: '秒' },
  { p: ['dashboardSeconds'], t: 'float', l: '看板刷新间隔', u: '秒' },
  { p: ['notify', 'browser'], t: 'bool', l: '浏览器桌面通知' },
  { p: ['notify', 'tray'], t: 'bool', l: '托盘气泡通知', u: '需托盘可用' },
];

function getPath(obj, path) {
  return path.reduce((o, k) => (o == null ? o : o[k]), obj);
}

function renderSettings(s) {
  $('settingsGrid').innerHTML = SETTINGS_FIELDS.map((f, i) => {
    const v = getPath(s, f.p);
    const id = `set${i}`;
    const attr = `id="${id}" data-path='${JSON.stringify(f.p)}' data-kind="${f.t}"`;
    let input;
    if (f.t === 'bool') input = `<input type="checkbox" ${attr} ${v ? 'checked' : ''} />`;
    else if (f.t === 'text') input = `<input type="text" ${attr} value="${esc(v ?? '')}" placeholder="例如 gpt-5" />`;
    else input = `<input type="number" ${attr} value="${v ?? ''}" step="${f.t === 'float' ? 0.5 : 1}" min="0" />`;
    return `<label class="setrow" for="${id}"><span class="sl">${esc(f.l)}${f.u ? ` <i>(${esc(f.u)})</i>` : ''}</span>${input}</label>`;
  }).join('');
}

async function fetchSettings(defaults) {
  const res = await fetch(`/api/settings${defaults ? '?defaults=1' : ''}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

async function postSettings(patch) {
  const res = await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!res.ok) return null;
  const s = await res.json();
  LIVE.settings = s;
  setupTimers(s);
  paintNotifyButton(s);
  return s;
}

function collectSettingsForm() {
  const patch = {};
  $('settingsGrid')
    .querySelectorAll('input[data-path]')
    .forEach((el) => {
      const path = JSON.parse(el.dataset.path);
      let val;
      if (el.dataset.kind === 'bool') val = el.checked;
      else if (el.dataset.kind === 'text') val = el.value.trim();
      else {
        val = Number(el.value);
        if (!isFinite(val) || val < 0) return;
      }
      let o = patch;
      for (let i = 0; i < path.length - 1; i++) o = o[path[i]] ?? (o[path[i]] = {});
      o[path[path.length - 1]] = val;
    });
  return patch;
}

// ---------- 浏览器通知授权 ----------

function paintNotifyButton(s) {
  const btn = $('notifyBtn');
  if (!('Notification' in window)) {
    btn.disabled = true;
    btn.textContent = '浏览器不支持通知';
    return;
  }
  const granted = Notification.permission === 'granted';
  btn.disabled = Notification.permission === 'denied';
  btn.textContent = granted
    ? s && s.notify && s.notify.browser
      ? '桌面通知已开启'
      : '开启桌面通知'
    : Notification.permission === 'denied'
      ? '通知已被拒绝'
      : '开启浏览器通知';
}

async function requestNotify() {
  if (!('Notification' in window)) return;
  const perm = await Notification.requestPermission();
  if (perm === 'granted') {
    const s = await postSettings({ notify: { browser: true } });
    if (s) renderSettings(s);
  }
  paintNotifyButton(LIVE.settings);
}

// ---------- 事件绑定 ----------

$('refresh').addEventListener('click', () => {
  refresh();
  fetchLive();
});
$('days').addEventListener('change', (e) => {
  state.days = Number(e.target.value);
  refresh();
});
$('modelFilter').addEventListener('change', (e) => {
  state.modelFilter = e.target.value;
  renderTurns(state.data);
});
$('modeFilter').addEventListener('change', (e) => {
  state.modeFilter = e.target.value;
  renderTurns(state.data);
});
$('traceManual').addEventListener('change', () => renderTrace(state.data));

$('clearFilter').addEventListener('click', () => {
  state.sessionFilter = '';
  state.modelFilter = '';
  state.modeFilter = '';
  $('modelFilter').value = '';
  $('modeFilter').value = '';
  renderSessions(state.data);
  renderTurns(state.data);
});

$('notifyBtn').addEventListener('click', requestNotify);

$('settingsBtn').addEventListener('click', async () => {
  const card = $('settingsCard');
  card.hidden = !card.hidden;
  if (!card.hidden) {
    try {
      const s = LIVE.settings || (await fetchSettings());
      LIVE.settings = s;
      renderSettings(s);
      $('settingsInfo').textContent = '改动后点「保存」生效，页面与托盘共用同一份设置。';
    } catch (err) {
      $('settingsInfo').textContent = `读取设置失败：${err.message}`;
    }
  }
});
$('settingsClose').addEventListener('click', () => {
  $('settingsCard').hidden = true;
});
$('settingsSave').addEventListener('click', async () => {
  const s = await postSettings(collectSettingsForm());
  $('settingsInfo').textContent = s ? '已保存，实时面板与提醒已按新配置运行。' : '保存失败，请稍后重试。';
  if (s) renderSettings(s);
  fetchLive();
});
$('settingsReset').addEventListener('click', async () => {
  try {
    const d = await fetchSettings(true);
    const s = await postSettings(d);
    if (s) renderSettings(s);
    $('settingsInfo').textContent = '已恢复默认设置。';
    fetchLive();
  } catch (err) {
    $('settingsInfo').textContent = `恢复默认失败：${err.message}`;
  }
});

$('csvAuto').addEventListener('click', autoFindCsv);

$('csvFile').addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const buf = reader.result;
    let text = new TextDecoder('utf-8').decode(buf);
    if (text.includes('\uFFFD')) {
      try { text = new TextDecoder('gbk').decode(buf); } catch { /* 保持 utf-8 结果 */ }
    }
    try {
      importCsv(text);
      state.csvSource = file.name;
      renderCsv();
    } catch (err) {
      $('csvInfo').textContent = `导入失败：${err.message}`;
    }
  };
  reader.readAsArrayBuffer(file);
});

$('clearCsv').addEventListener('click', () => {
  state.csv = null;
  state.csvSource = null;
  $('csvFile').value = '';
  $('csvCandidates').innerHTML = '';
  renderCsv();
});

// 实时接口轻、跑得勤；看板接口重、跑得疏。这样既秒级跳动又不会一直狂刷解析。
function setupTimers(s) {
  if (state.timer) clearInterval(state.timer);
  if (LIVE.timer) clearInterval(LIVE.timer);
  state.timer = null;
  LIVE.timer = null;
  if (!$('autoRefresh').checked) return;
  const poll = Math.max(1, Number(s && s.pollSeconds) || 2) * 1000;
  const dash = Math.max(5, Number(s && s.dashboardSeconds) || 30) * 1000;
  LIVE.timer = setInterval(fetchLive, poll);
  state.timer = setInterval(refresh, dash);
}
$('autoRefresh').addEventListener('change', () => setupTimers(LIVE.settings));

async function boot() {
  liveClock();
  LIVE.clockTimer = setInterval(liveClock, 1000);
  let s = null;
  try {
    s = await fetchSettings();
  } catch {
    s = null;
  }
  LIVE.settings = s;
  if (s) renderSettings(s);
  paintNotifyButton(s);
  setupTimers(s);
  fetchLive();
  refresh();
}

boot();
