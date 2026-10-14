/* ============================================================
   Chronos 前端逻辑 —— 时钟 / 闹钟 / 计时 / 秒表
   通过 window.pywebview.api 调用 Python 后端；
   直接在浏览器打开时回退到 MOCK，便于预览设计。
   ============================================================ */
'use strict';

const WEEKDAY_CN = ['一', '二', '三', '四', '五', '六', '日'];
const C = 879; // 环周长 2πr (r=140)

/* ---------- 浏览器预览用的 MOCK 后端 ---------- */
const MOCK = (() => {
  let alarms = [
    { id: 1, time: '07:00', label: '起床', enabled: true, days: [0,1,2,3,4,5,6], once: false, china_workday: false, ringtone: '霓虹脉冲.wav', snooze: false, volume: 70 },
    { id: 2, time: '09:30', label: '团队站会', enabled: true, days: [0,1,2,3,4], once: false, china_workday: true, ringtone: '深海回响.wav', snooze: false, volume: 70 },
    { id: 3, time: '12:30', label: '午餐提醒', enabled: false, days: [0,1,2,3,4,5,6], once: false, china_workday: false, ringtone: '星轨.wav', snooze: false, volume: 40 },
    { id: 4, time: '19:00', label: '运动', enabled: true, days: [0,2,4], once: false, china_workday: false, ringtone: '霓虹脉冲.wav', snooze: true, volume: 100 },
    { id: 5, time: '22:30', label: '睡眠', enabled: false, days: [0,1,2,3,4,5,6], once: false, china_workday: false, ringtone: '无声震动.wav', snooze: false, volume: 70 },
  ];
  let nextId = 6;
  let muted = true;
  let ringtones = [
    { name: '霓虹脉冲.wav', custom: false },
    { name: '深海回响.wav', custom: false },
    { name: '星轨.wav', custom: false },
    { name: '无声震动.wav', custom: false },
  ];
  const ok = (v) => Promise.resolve(v);
  return {
    get_alarms: () => ok(alarms.map(a => ({ ...a }))),
    get_next_alarm: () => {
      const en = alarms.filter(a => a.enabled);
      return ok(en.length ? { has: true, time: en[0].time, label: en[0].label, repeat: repeatShort(en[0]), remaining: '8 小时 12 分' } : { has: false });
    },
    get_settings: () => ok({ muted }),
    set_muted: (m) => { muted = !!m; return ok({ muted }); },
    add_alarm: (time, label, days, ringtone, snooze, volume, chinaWorkday) => {
      alarms.push({ id: nextId++, time, label, enabled: true, days, once: false, china_workday: !!chinaWorkday, ringtone, snooze, volume });
      return ok(alarms.map(a => ({ ...a })));
    },
    update_alarm: (id, fields) => {
      const a = alarms.find(x => x.id === id);
      if (a) Object.assign(a, fields);
      return ok(alarms.map(x => ({ ...x })));
    },
    toggle_alarm: (id) => {
      const a = alarms.find(x => x.id === id);
      if (a) a.enabled = !a.enabled;
      return ok(alarms.map(x => ({ ...x })));
    },
    delete_alarm: (id) => { alarms = alarms.filter(x => x.id !== id); return ok(alarms.map(x => ({ ...x }))); },
    test_notify: () => ok(true),
    get_ringtones: () => ok(ringtones.map(r => ({ ...r }))),
    upload_ringtone: (filename, data) => {
      const name = filename.split(/[\\/]/).pop();
      ringtones.push({ name, custom: true });
      return ok({ list: ringtones.map(r => ({ ...r })), saved: name });
    },
    delete_ringtone: (name) => { ringtones = ringtones.filter(r => r.name !== name || !r.custom); return ok(ringtones.map(r => ({ ...r }))); },
    play_ringtone: () => ok(true),
  };
})();

let API = null;

/* ---------- 工具 ---------- */
const $ = (id) => document.getElementById(id);
function pad(n, w = 2) { return String(n).padStart(w, '0'); }
function showFallback(msg) {
  const el = $('fallback');
  el.textContent = msg;
  el.style.display = 'block';
  clearTimeout(showFallback._t);
  showFallback._t = setTimeout(() => { el.style.display = 'none'; }, 3200);
}

/* ---------- 导航 ---------- */
const titleMap = {
  clock:    ['世界时钟', 'CHRONOS · DEEP BLUE TECH UI'],
  alarms:   ['我的闹钟', '已启用 0 / 共 0 个'],
  form:     ['新建闹钟', '设置时间、重复与铃声'],
  timer:    ['专注计时', 'POMODORO · 倒计时'],
  stopwatch:['秒表', 'LAP 计时'],
};
function switchView(v) {
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  document.querySelectorAll('.view').forEach(s => s.classList.remove('active'));
  $('view-' + v).classList.add('active');
  document.getElementById('viewTitle').textContent = titleMap[v][0];
  document.getElementById('viewSub').textContent = titleMap[v][1];
}

/* ---------- 实时时钟 ---------- */
function tickClock() {
  const d = new Date();
  $('hh').textContent = pad(d.getHours());
  $('mm').textContent = pad(d.getMinutes());
  $('ss').textContent = pad(d.getSeconds());
  $('liveClock').textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  $('dateLine').textContent = `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 星期${WEEKDAY_CN[(d.getDay() + 6) % 7]}`;
}

/* ---------- 闹钟列表 ---------- */
function repeatShort(a) {
  if (a.china_workday) return '工作日（调休）';
  const days = (a.days || []).slice().sort((x, y) => x - y);
  if (a.once) return '仅一次';
  if (!days.length) return '未设置';
  if (days.length === 7) return '每天';
  if (days.join() === '0,1,2,3,4') return '工作日';
  if (days.join() === '5,6') return '周末';
  return '周' + days.map(d => WEEKDAY_CN[d]).join(' ');
}

function renderAlarms(list) {
  const grid = $('alarmGrid');
  grid.innerHTML = '';
  if (!list.length) {
    grid.innerHTML = '<div class="empty-hint">还没有闹钟，点击右上角「＋ 新建闹钟」开始吧。</div>';
  }
  list.forEach(a => {
    const el = document.createElement('div');
    el.className = 'alarm-item' + (a.enabled ? '' : ' off');
    el.dataset.id = a.id;
    el.innerHTML = `
      <button class="alarm-del" title="删除" data-id="${a.id}">✕</button>
      <div class="alarm-time">${a.time}</div>
      <label class="switch alarm-switch" data-toggle="${a.id}">
        <input type="checkbox" ${a.enabled ? 'checked' : ''} data-toggle="${a.id}">
        <span class="track"></span><span class="knob"></span>
      </label>
      <div class="alarm-meta">
        <div class="name">${escapeHtml(a.label)}${a.active_today ? '<span class="badge">今天</span>' : ''}</div>
        <div class="repeat">${repeatShort(a)}</div>
      </div>`;
    grid.appendChild(el);
  });
  // 计数
  const enabled = list.filter(a => a.enabled).length;
  titleMap.alarms[1] = `已启用 ${enabled} / 共 ${list.length} 个`;
  if ($('view-alarms').classList.contains('active')) {
    document.getElementById('viewSub').textContent = titleMap.alarms[1];
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------- 下一个闹钟 + 今日安排 ---------- */
async function refreshClockPanel() {
  try {
    const next = await API.get_next_alarm();
    if (next.has) {
      $('nextAlarm').firstChild.textContent = next.time;
      $('nextAlarmSub').textContent = `${next.label} · ${next.repeat} · 还有 ${next.remaining}`;
    } else {
      $('nextAlarm').firstChild.textContent = '--:--';
      $('nextAlarmSub').textContent = '暂无启用的闹钟';
    }
    const list = await API.get_alarms();
    const today = list.filter(a => a.enabled && a.active_today);
    const box = $('todayList');
    if (!today.length) {
      box.innerHTML = '<div class="ring-row" style="color:var(--text-faint)">今日无安排</div>';
    } else {
      box.innerHTML = today.map(a =>
        `<div class="ring-row"><div class="ring-ico">⏰</div> ${a.time} ${escapeHtml(a.label)} · ${repeatShort(a)}</div>`
      ).join('');
    }
  } catch (e) { /* ignore */ }
}

/* ---------- 统一刷新 ---------- */
async function refreshAll() {
  try {
    const list = await API.get_alarms();
    renderAlarms(list);
    await refreshClockPanel();
  } catch (e) { /* ignore */ }
}

/* ---------- 表单 ---------- */
let editingId = null;
let formChinaWorkday = false;

function selectedDays() {
  return Array.from(document.querySelectorAll('#fDays .chip[data-day].selected'))
    .map(c => parseInt(c.dataset.day, 10)).sort((a, b) => a - b);
}
function setDays(days) {
  document.querySelectorAll('#fDays .chip[data-day]').forEach(c => {
    c.classList.toggle('selected', days.includes(parseInt(c.dataset.day, 10)));
  });
}
function setChinaWorkday(v) {
  formChinaWorkday = !!v;
  document.querySelectorAll('#fDays .chip[data-quick="wd"]').forEach(c => c.classList.toggle('selected', formChinaWorkday));
}

function bindDayChips() {
  document.querySelectorAll('#fDays .chip').forEach(chip => {
    chip.addEventListener('click', () => {
      if (chip.dataset.quick) {
        const q = chip.dataset.quick;
        const all = [0,1,2,3,4,5,6];
        const wd = [0,1,2,3,4];
        const we = [5,6];
        if (q === 'wd') {
          // 工作日：按中国调休日历计算，前端只存标志，显示为周一到周五
          const already = formChinaWorkday && wd.every(d => document.querySelector(`#fDays .chip[data-day="${d}"]`).classList.contains('selected'));
          if (already) {
            setDays([]);
            setChinaWorkday(false);
          } else {
            setDays(wd);
            setChinaWorkday(true);
          }
          return;
        }
        const target = q === 'all' ? all : we;
        setChinaWorkday(false);
        const already = target.every(d => document.querySelector(`#fDays .chip[data-day="${d}"]`).classList.contains('selected'));
        setDays(already ? [] : target);
      } else {
        chip.classList.toggle('selected');
        setChinaWorkday(false);
      }
    });
  });
}

function goForm(id) {
  editingId = id || null;
  if (id) {
    $('formTitle').textContent = '编辑闹钟';
    API.get_alarms().then(list => {
      const a = list.find(x => x.id === id);
      if (a) {
        $('fTime').value = a.time;
        $('fLabel').value = a.label;
        setChinaWorkday(a.china_workday);
        setDays(a.china_workday ? [0,1,2,3,4] : (a.days || []));
        populateRingtones(a.ringtone || '霓虹脉冲');
        $('fSnooze').value = a.snooze ? 'on' : 'off';
        $('fVolume').value = String(a.volume || 70);
      }
    });
  } else {
    $('formTitle').textContent = '新建闹钟';
    $('fTime').value = '07:00';
    $('fLabel').value = '起床';
    setChinaWorkday(true);
    setDays([0,1,2,3,4]);
    populateRingtones('霓虹脉冲');
    $('fSnooze').value = 'off';
    $('fVolume').value = '70';
  }
  switchView('form');
}

async function saveForm() {
  const time = $('fTime').value || '07:00';
  const label = ($('fLabel').value || '').trim() || '闹钟';
  const days = selectedDays();
  if (!days.length && !formChinaWorkday) { showFallback('请至少选择一天'); return; }
  const ringtone = $('fRingtone').value;
  const snooze = $('fSnooze').value === 'on';
  const volume = parseInt($('fVolume').value, 10) || 70;
  try {
    if (editingId) {
      await API.update_alarm(editingId, { time, label, days, china_workday: formChinaWorkday, ringtone, snooze, volume });
    } else {
      await API.add_alarm(time, label, days, ringtone, snooze, volume, formChinaWorkday);
    }
    await refreshAll();
    switchView('alarms');
  } catch (e) { showFallback('保存失败'); }
}

/* ---------- 计时器 ---------- */
let tTotal = 1500, tLeft = 1500, tTimer = null;
function drawTimer() {
  $('timerText').textContent = `${pad(Math.floor(tLeft / 60))}:${pad(tLeft % 60)}`;
  $('timerArc').setAttribute('stroke-dashoffset', C * (1 - tLeft / tTotal));
}
function timerStart() {
  if (tTimer) return;
  if (tLeft <= 0) tLeft = tTotal;
  tTimer = setInterval(() => {
    if (tLeft > 0) { tLeft--; drawTimer(); }
    else { clearInterval(tTimer); tTimer = null; showFallback('⏰ 计时结束'); }
  }, 1000);
}
function timerPause() { if (tTimer) { clearInterval(tTimer); tTimer = null; } }
function timerReset() { timerPause(); tLeft = tTotal; drawTimer(); }
function bindTimer() {
  $('timerBtn').onclick = timerStart;
  $('timerPause').onclick = timerPause;
  $('timerReset').onclick = timerReset;
  document.querySelectorAll('#view-timer .preset-row .chip').forEach(c => {
    c.onclick = () => { timerPause(); tTotal = tLeft = parseInt(c.dataset.min, 10) * 60; drawTimer(); };
  });
}

/* ---------- 秒表 ---------- */
let swMs = 0, swTimer = null, laps = [];
function fmtSw(ms) {
  const m = Math.floor(ms / 60000), s = Math.floor(ms % 60000 / 1000), c = Math.floor(ms % 1000 / 10);
  return `${pad(m)}:${pad(s)}.${pad(c)}`;
}
function drawSw() {
  $('swText').textContent = fmtSw(swMs);
  $('swArc').setAttribute('stroke-dashoffset', C * (1 - (swMs % 60000) / 60000));
}
function swStart() {
  if (swTimer) return;
  swTimer = setInterval(() => { swMs += 37; drawSw(); }, 37);
}
function swPause() { if (swTimer) { clearInterval(swTimer); swTimer = null; } }
function swReset() { swPause(); swMs = 0; laps = []; drawSw(); $('lapList').innerHTML = ''; }
function swLap() {
  laps.unshift({ n: laps.length + 1, t: fmtSw(swMs) });
  $('lapList').innerHTML = laps.map(l => `<div class="lap"><span>圈 ${l.n}</span><span>${l.t}</span></div>`).join('');
}
function bindStopwatch() {
  $('swBtn').onclick = () => { swTimer ? swPause() : swStart(); };
  $('swReset').onclick = swReset;
  $('swLap').onclick = swLap;
}

/* ---------- 声音开关 ---------- */
function bindSound() {
  const sw = $('soundToggle');
  API.get_settings().then(s => { sw.checked = !s.muted; }).catch(() => {});
  sw.onchange = () => {
    API.set_muted(!sw.checked).catch(() => {});
  };
}

/* ---------- 铃声 ---------- */
function populateRingtones(selectedName) {
  API.get_ringtones().then(list => {
    const sel = $('fRingtone');
    const current = sel.value || selectedName || '霓虹脉冲.wav';
    sel.innerHTML = '';
    list.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.name;
      opt.textContent = r.name;
      sel.appendChild(opt);
    });
    // 兼容旧数据只存显示名的情况
    const exists = Array.from(sel.options).some(o => o.value === current);
    sel.value = exists ? current : (list[0] && list[0].name);
    renderRingtoneTags(list);
  }).catch(() => {});
}

function renderRingtoneTags(list) {
  const box = $('ringtoneList');
  box.innerHTML = '';
  list.filter(r => r.custom).forEach(r => {
    const tag = document.createElement('span');
    tag.className = 'ringtone-tag';
    tag.innerHTML = `<button class="play" title="试听" data-play="${escapeHtml(r.name)}">▶</button> ${escapeHtml(r.name)} <button title="删除" data-del="${escapeHtml(r.name)}">✕</button>`;
    box.appendChild(tag);
  });
}

function bindRingtoneUpload() {
  const fileInput = $('fRingtoneFile');
  const btn = $('btnUploadRingtone');
  btn.onclick = () => fileInput.click();
  fileInput.onchange = () => {
    const file = fileInput.files[0];
    if (!file) return;
    if (file.size > 25 * 1024 * 1024) { showFallback('音频文件不能超过 25MB'); return; }
    const reader = new FileReader();
    reader.onload = (e) => {
      API.upload_ringtone(file.name, e.target.result).then(res => {
        const list = Array.isArray(res) ? res : (res.list || []);
        const saved = (res && res.saved) ? res.saved : file.name;
        renderRingtoneTags(list);
        const names = list.map(r => r.name);
        const sel = $('fRingtone');
        sel.innerHTML = '';
        list.forEach(r => {
          const opt = document.createElement('option');
          opt.value = r.name; opt.textContent = r.name;
          sel.appendChild(opt);
        });
        sel.value = names.includes(saved) ? saved : (list[0] && list[0].name);
        showFallback('音频已上传');
      }).catch(() => showFallback('上传失败'));
    };
    reader.readAsDataURL(file);
  };
  $('ringtoneList').addEventListener('click', (e) => {
    const t = e.target;
    if (t.dataset.play) {
      API.play_ringtone(t.dataset.play, parseInt($('fVolume').value, 10) || 70).catch(() => {});
    } else if (t.dataset.del) {
      API.delete_ringtone(t.dataset.del).then(list => {
        renderRingtoneTags(list);
        populateRingtones();
      }).catch(() => {});
    }
  });
}

/* ---------- 初始化 ---------- */
function initUI() {
  // 导航
  document.querySelectorAll('.nav-btn').forEach(b => b.onclick = () => switchView(b.dataset.view));
  $('btnNew').onclick = () => goForm(null);
  $('btnBack').onclick = () => switchView('alarms');
  $('btnCancel').onclick = () => switchView('alarms');
  $('btnSave').onclick = saveForm;
  bindDayChips();
  bindTimer();
  bindStopwatch();
  bindSound();
  bindRingtoneUpload();
  populateRingtones();
  // 列表事件委托
  $('alarmGrid').addEventListener('click', (e) => {
    const t = e.target;
    // 删除按钮
    const delBtn = t.closest('.alarm-del');
    if (delBtn) {
      e.stopPropagation();
      API.delete_alarm(parseInt(delBtn.dataset.id, 10)).then(refreshAll).catch(() => {});
      return;
    }
    // 开关
    const sw = t.closest('.alarm-switch');
    if (sw) {
      e.stopPropagation();
      API.toggle_alarm(parseInt(sw.dataset.toggle, 10)).then(refreshAll).catch(() => {});
      return;
    }
    // 点击卡片其它区域 -> 编辑
    const item = t.closest('.alarm-item');
    if (item) {
      goForm(parseInt(item.dataset.id, 10));
    }
  });
  // 实时时钟
  tickClock();
  setInterval(tickClock, 1000);
  drawTimer(); drawSw();
  refreshAll();
}

function boot() {
  API = (window.pywebview && window.pywebview.api) ? window.pywebview.api : MOCK;
  initUI();
}

if (window.pywebview && window.pywebview.api) {
  boot();
} else if (window.pywebview) {
  window.addEventListener('pywebviewready', boot);
} else {
  document.addEventListener('DOMContentLoaded', boot);
}
