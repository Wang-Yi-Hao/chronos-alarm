/* Chronos 桌面悬浮窗 —— 展开/折叠、时钟、已开启闹钟列表、临近与响铃状态 */
'use strict';

const $ = (id) => document.getElementById(id);
const pad = (n) => String(n).padStart(2, '0');
const WD = ['日', '一', '二', '三', '四', '五', '六'];

let API = null;
let booted = false;
let collapsed = false;
let data = { alarms: [], ringing: null };
let ringTimer = null;

/* ---------------- 时钟 ---------------- */
let timeOffsetMs = 0;
window.setTimeOffset = (ms) => {
  const v = Number(ms);
  timeOffsetMs = isFinite(v) ? v : 0;
  try { tick(); } catch (e) {}
};

function tick() {
  const d = new Date(Date.now() + timeOffsetMs);
  const hh = pad(d.getHours()), mm = pad(d.getMinutes());
  $('hm').textContent = hh + ':' + mm;
  $('sec').textContent = pad(d.getSeconds());
  $('cTime').textContent = hh + ':' + mm;
  $('cSec').textContent = pad(d.getSeconds());
  $('dateLine').textContent =
    `${d.getMonth() + 1}月${d.getDate()}日 · 星期${WD[d.getDay()]}`;
  paintRemain();
}

/* ---------------- 数据渲染 ---------------- */
function fmtRemain(ms) {
  const m = Math.max(0, Math.round(ms / 60000));
  if (m < 1) return '不到 1 分钟';
  if (m < 60) return m + ' 分钟';
  const h = Math.floor(m / 60), r = m % 60;
  return r ? h + ' 小时 ' + r + ' 分' : h + ' 小时';
}
function fmtRemainShort(ms) {
  const m = Math.max(0, Math.round(ms / 60000));
  if (m < 60) return m + 'm';
  const h = Math.floor(m / 60), r = m % 60;
  return r ? h + 'h' + r + 'm' : h + 'h';
}
function dayWord(ts) {
  const now = new Date();
  const d = new Date(ts * 1000);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = Math.round((target - today) / 86400000);
  if (diff <= 0) return '今天';
  if (diff === 1) return '明天';
  return '周' + WD[d.getDay()];
}

function sortedAlarms() {
  return (data.alarms || [])
    .filter(a => a.enabled !== false)
    .slice()
    .sort((a, b) => (a.next_ts || 0) - (b.next_ts || 0));
}

function render() {
  const list = sortedAlarms();
  const box = $('list');
  const empty = $('empty');
  $('badge').textContent = String(list.length);
  $('badge').style.display = list.length ? 'grid' : 'none';

  if (!list.length) {
    box.innerHTML = '';
    box.style.display = 'none';
    empty.hidden = false;
    $('listTitle').textContent = '已开启的闹钟';
    $('listTitle').parentElement.classList.remove('soon');
    $('widget').classList.remove('soon');
    document.querySelector('.c-label').textContent = '闹钟';
    $('cNext').textContent = '暂无已开启';
    return;
  }
  empty.hidden = true;
  box.style.display = 'flex';

  box.innerHTML = list.map((a, i) => {
    const isNext = i === 0;
    return `<div class="item${isNext ? ' next' : ''}" data-id="${a.id}">
      <span class="t">${a.time}</span>
      <div class="mid">
        <div class="name">${esc(a.label)}</div>
        <div class="sub">${esc(a.repeat || '')}${isNext ? ' · 下一个' : ''}</div>
      </div>
      <span class="right" data-ts="${a.next_ts || 0}" data-next="${isNext ? 1 : 0}"></span>
    </div>`;
  }).join('');

  const n = list[0];
  document.querySelector('.c-label').textContent = '下一个';
  $('cNext').textContent = `${n.time} ${n.label}`;
  paintRemain();
}

/* 剩余时间/今天明天，随时间刷新（不请求后端） */
function paintRemain() {
  const now = Date.now();
  document.querySelectorAll('#list .right').forEach(el => {
    const ts = Number(el.dataset.ts) * 1000;
    if (!ts) { el.textContent = ''; return; }
    if (el.dataset.next === '1') {
      const left = ts - now;
      el.textContent = left > 0 ? fmtRemainShort(left) : '即将响铃';
    } else {
      el.textContent = dayWord(ts / 1000);
    }
  });

  const list = sortedAlarms();
  const head = $('listTitle').parentElement;
  if (list.length && list[0].next_ts) {
    const left = list[0].next_ts * 1000 - now;
    if (left > 0 && left <= 30 * 60000) {
      $('listTitle').textContent = '即将响铃 · ' + fmtRemain(left) + '后';
      head.classList.add('soon');
      $('widget').classList.add('soon');
    } else {
      $('listTitle').textContent = '已开启的闹钟';
      head.classList.remove('soon');
      $('widget').classList.remove('soon');
    }
  }
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ---------------- 响铃态 ---------------- */
function setRinging(r) {
  data.ringing = r || null;
  const w = $('widget');
  if (r) {
    w.classList.add('ringing');
    clearTimeout(ringTimer);
    const left = Math.max(0, (r.until_ms || 0) - Date.now());
    ringTimer = setTimeout(() => { if (API) API.widget_ring_state().then(applyState).catch(() => {}); }, left + 300);
  } else {
    w.classList.remove('ringing');
  }
}

function applyState(s) {
  if (!s) return;
  if (typeof s.time_offset === 'number') {
    timeOffsetMs = Math.round(s.time_offset * 1000);
  }
  collapsed = !!s.collapsed;
  document.body.classList.toggle('mode-collapsed', collapsed);
  document.querySelectorAll('[data-act="pin"]').forEach((b) => {
    b.classList.toggle('on', !!s.on_top);
    b.title = s.on_top ? '取消置顶' : '置顶';
  });
  if (s.alarms) {
    data.alarms = s.alarms;
    render();
  }
  if (s.ringing !== undefined) setRinging(s.ringing);
}

async function refresh() {
  if (!API) return;
  try {
    const s = await API.widget_state();
    applyState(s);
  } catch (e) { /* ignore */ }
}

/* ---------------- 交互 ---------------- */
function bind() {
  $('btnExpand').onclick = async () => {
    if (!API) return;
    try {
      const s = await API.widget_toggle_collapse();
      applyState(s);
    } catch (e) {}
  };
  $('btnOpen').onclick = () => { if (API) API.open_main_window().catch(() => {}); };
  $('btnStop').onclick = () => { if (API) API.stop_ringing().then(applyState).catch(() => {}); };
  $('btnSnooze').onclick = () => { if (API) API.snooze_ringing().then(applyState).catch(() => {}); };

  // 标题条与悬停控制条共用 data-act（置顶 / 折叠 / 隐藏）
  document.querySelectorAll('[data-act]').forEach((b) => {
    b.onclick = async (e) => {
      e.stopPropagation();
      if (!API) return;
      try {
        if (b.dataset.act === 'pin') applyState(await API.widget_toggle_on_top());
        else if (b.dataset.act === 'collapse') applyState(await API.widget_toggle_collapse());
        else if (b.dataset.act === 'hide') await API.widget_hide();
      } catch (err) {}
    };
  });

  // 双击标题条折叠 / 展开（与设计稿一致）
  document.querySelector('.w-head').addEventListener('dblclick', async () => {
    if (!API) return;
    try { applyState(await API.widget_toggle_collapse()); } catch (e) {}
  });
  document.querySelector('.compact').addEventListener('dblclick', async () => {
    if (!API) return;
    try { applyState(await API.widget_toggle_collapse()); } catch (e) {}
  });

  // 点击条目：跳主窗口并定位
  $('list').addEventListener('click', (e) => {
    const item = e.target.closest('.item');
    if (!item || !API) return;
    API.open_main_alarm(parseInt(item.dataset.id, 10)).catch(() => {});
  });

  const w = $('widget');
  w.addEventListener('mouseenter', () => { if (API) API.widget_hover(true).catch(() => {}); });
  w.addEventListener('mouseleave', () => { if (API) API.widget_hover(false).catch(() => {}); });

  // 后端推送
  window.onWidgetRefresh = () => { refresh(); };
  window.onRing = (r) => { setRinging(r); if (r) refresh(); };
}

/* ---------------- 启动 ---------------- */
function launch(api) {
  if (booted) return;
  booted = true;
  API = api;
  bind();
  tick();
  setInterval(tick, 1000);
  setInterval(refresh, 5000);   // 兜底轮询（主窗口改动主要由后端推送）
  refresh();
}

function boot() {
  if (booted) return;
  if (window.pywebview && window.pywebview.api) { launch(window.pywebview.api); return; }
  let tries = 0;
  const t = () => {
    if (booted) return;
    if (window.pywebview && window.pywebview.api) { launch(window.pywebview.api); return; }
    if (++tries > 60) { launch({}); return; }
    setTimeout(t, 50);
  };
  window.addEventListener('pywebviewready', t);
  t();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
