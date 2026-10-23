# -*- coding: utf-8 -*-
"""Chronos 闹钟 —— pywebview 桌面外壳。

前端（ui/index.html + app.js + style.css）实现资料库 Chronos 深蓝科技风设计；
本文件提供原生窗口、Python 后端 API（闹钟存取 / 通知 / 设置）与后台到点检查线程。
"""
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import webview

from core import (
    load_alarms, save_alarms, load_settings, save_settings,
    next_fire, active_today, is_due_now, repeat_label, fmt_remaining, fmt_next,
    show_toast, acquire_single_instance, next_id, selftest,
    get_ringtones, upload_ringtone, delete_ringtone, play_alarm_sound,
    start_alarm_sound,
    get_autostart, set_autostart, sync_alarm_tasks,
    load_fired, save_fired, stamp_once_date, expired_once,
    current_time, sync_time, time_sync_state, time_offset, set_system_time, TIME_SYNC_INTERVAL,
)

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))  # 数据目录（闹钟/设置就近存放）
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _setup_logging():
    """配置日志：输出到文件（必要时也到控制台），便于排查托盘、关闭事件等问题。"""
    log_path = os.path.join(APP_DIR, "chronos.log")
    try:
        handlers = [logging.FileHandler(log_path, encoding="utf-8", mode="a")]
        # 开发模式/控制台可用时追加控制台输出
        if not getattr(sys, "frozen", False) or sys.stdout:
            handlers.append(logging.StreamHandler(sys.stdout))
    except Exception:
        handlers = [logging.StreamHandler(sys.stdout)] if sys.stdout else []
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


_setup_logging()


def _resolve_bundle_dir():
    """冻结环境下 PyInstaller 6 的 onedir 把资源放到 _internal 子目录；
    onefile 提取到 sys._MEIPASS；开发模式则在脚本同目录。依次探测 ui/index.html。"""
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(meipass)
    candidates.append(os.path.join(exe_dir, "_internal"))
    candidates.append(exe_dir)
    candidates.append(script_dir)
    for c in candidates:
        if os.path.exists(os.path.join(c, "ui", "index.html")):
            return c
    return exe_dir


if getattr(sys, "frozen", False):
    BUNDLE_DIR = _resolve_bundle_dir()
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

UI_DIR = os.path.join(BUNDLE_DIR, "ui")
HTML_PATH = os.path.join(UI_DIR, "index.html")
WIDGET_HTML_PATH = os.path.join(UI_DIR, "widget.html")
ICON_PATH = os.path.join(BUNDLE_DIR, "icon", "appicon.ico")

# 桌面悬浮窗尺寸（见设计稿：展开 340×376 / 折叠 340×62 / 圆角 22）
WIDGET_WIDTH = 340
WIDGET_HEIGHT = 376
WIDGET_HEIGHT_COLLAPSED = 62
WIDGET_MARGIN = 24          # 距屏幕工作区右下角的边距
RING_SECONDS = 30           # 响铃持续时长（与铃声播放上限一致）
# 开发模式下如果 BUNDLE_DIR 没有 icon，回退到脚本目录
if not os.path.exists(ICON_PATH):
    ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon", "appicon.ico")


class ChronosAPI:
    """暴露给前端 JS 的接口（window.pywebview.api）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.alarms = load_alarms()
        self.settings = load_settings()
        self._next_id = next_id(self.alarms)
        self._last_fired = {}
        self._stop = threading.Event()
        self._quitting = False  # 托盘退出标志：True 时关闭窗口才真正退出
        self._widget_win = None   # 桌面悬浮窗（main() 中创建后回填）
        self._main_win = None     # 主窗口
        self._ring = None        # 当前响铃态 {seq,id,label,until,proc}
        self._ring_seq = 0
        self._normalize_widget_cfg()
        self._purge_expired_once()

    def _normalize_widget_cfg(self):
        """补齐悬浮窗配置默认值并落盘（老版本 settings.json 可能没有 widget 字段）。"""
        cfg = dict(self._widget_cfg())
        cfg.setdefault("on_top", True)
        cfg.setdefault("collapsed", False)
        cfg.setdefault("visible", True)
        if cfg != self._widget_cfg():
            self.settings["widget"] = cfg
            try:
                save_settings(self.settings)
            except Exception:
                logging.exception("[widget] 写入默认配置失败")

    def _purge_expired_once(self):
        """启动时把已过期的一次性闹钟标记为关闭，避免列表显示"开启"却永不响。"""
        changed = False
        for a in self.alarms:
            try:
                if expired_once(a):
                    a["enabled"] = False
                    changed = True
            except Exception:
                continue
        if changed:
            try:
                save_alarms(self.alarms)
            except Exception:
                logging.exception("[alarm] 停用过期一次性闹钟失败")

    # ---------------- 供前端调用 ----------------
    def get_alarms(self):
        now = current_time()
        out = []
        for a in sorted(self.alarms, key=lambda x: (x["time"], x["id"])):
            nf = next_fire(a, now) if a["enabled"] else None
            out.append({
                "id": a["id"],
                "time": a["time"],
                "label": a["label"],
                "enabled": a["enabled"],
                "days": a["days"],
                "once": a["once"],
                "china_workday": bool(a.get("china_workday", False)),
                "ringtone": a.get("ringtone", "霓虹脉冲"),
                "snooze": a.get("snooze", False),
                "volume": a.get("volume", 70),
                "repeat": repeat_label(a),
                "active_today": active_today(a, now),
                "next": fmt_next(nf, now) if nf else "-",
            })
        return out

    def get_next_alarm(self):
        now = current_time()
        best, best_t = None, None
        for a in self.alarms:
            if not a["enabled"]:
                continue
            nf = next_fire(a, now)
            if nf and (best_t is None or nf < best_t):
                best, best_t = a, nf
        if best is None:
            return {"has": False}
        return {
            "has": True,
            "time": best["time"],
            "label": best["label"],
            "repeat": repeat_label(best),
            "remaining": fmt_remaining(best_t - now),
        }

    def get_settings(self):
        return {
            "muted": bool(self.settings.get("muted", True)),
            "autostart": get_autostart(),
            "widget_visible": bool(self._widget_cfg().get("visible", True)),
            "widget_enabled": self._widget_win is not None,
            "time_sync": time_sync_state(),
        }

    def sync_time_now(self):
        """手动立即校时（设置页按钮用）。"""
        st = sync_time(apply_system_clock=True)
        if st.get("synced"):
            logging.info("[time] 手动校时：%s 偏差 %+.2fs", st.get("server"), st.get("raw_offset") or 0.0)
            self._push_time_offset()
            self._push_alarms()
        return st

    def sync_system_time_elevated(self):
        """请求管理员权限把本机系统时间改成服务器时间（会弹 UAC）。"""
        ok = _try_elevate_system_time(self)
        return {"ok": ok, "time_sync": time_sync_state()}

    def set_muted(self, muted):
        self.settings["muted"] = bool(muted)
        save_settings(self.settings)
        return self.get_settings()

    def set_autostart(self, enabled):
        ok = set_autostart(bool(enabled))
        return {"autostart": get_autostart(), "ok": ok}

    def add_alarm(self, time, label, days, ringtone="霓虹脉冲", snooze=False, volume=70,
                  china_workday=False, once=False):
        try:
            datetime.strptime(time, "%H:%M")
        except Exception:
            return self.get_alarms()
        days = [int(d) for d in (days or []) if 0 <= int(d) <= 6]
        china_workday = bool(china_workday)
        once = bool(once)
        if once:
            china_workday = False
            if not days:
                days = [0, 1, 2, 3, 4, 5, 6]
        if not days and not china_workday:
            return self.get_alarms()
        if china_workday and not days:
            days = [0, 1, 2, 3, 4]
        with self.lock:
            alarm = {
                "id": self._next_id,
                "time": time,
                "label": str(label or "闹钟"),
                "enabled": True,
                "days": days,
                "once": once,
                "china_workday": china_workday,
                "ringtone": str(ringtone or "霓虹脉冲"),
                "snooze": bool(snooze),
                "volume": max(0, min(100, int(volume if volume is not None else 70))),
            }
            stamp_once_date(alarm)
            self.alarms.append(alarm)
            self._next_id += 1
            save_alarms(self.alarms)
        self._sync_tasks()
        return self.get_alarms()

    def update_alarm(self, alarm_id, fields):
        fields = fields or {}
        with self.lock:
            for a in self.alarms:
                if a["id"] == alarm_id:
                    old_time, old_once = a.get("time"), bool(a.get("once"))
                    if "time" in fields:
                        try:
                            datetime.strptime(fields["time"], "%H:%M")
                            a["time"] = fields["time"]
                        except Exception:
                            pass
                    if "label" in fields:
                        a["label"] = str(fields["label"] or "闹钟")
                    if "days" in fields:
                        d = [int(x) for x in fields["days"] if 0 <= int(x) <= 6]
                        if d:
                            a["days"] = d
                    if "china_workday" in fields:
                        a["china_workday"] = bool(fields["china_workday"])
                        if a["china_workday"] and not a["days"]:
                            a["days"] = [0, 1, 2, 3, 4]
                    if "once" in fields:
                        a["once"] = bool(fields["once"])
                        if a["once"]:
                            # 仅一次与调休工作日互斥
                            a["china_workday"] = False
                            if not a.get("days"):
                                a["days"] = [0, 1, 2, 3, 4, 5, 6]
                        else:
                            a.pop("once_date", None)
                    if "ringtone" in fields:
                        a["ringtone"] = str(fields["ringtone"])
                    if "snooze" in fields:
                        a["snooze"] = bool(fields["snooze"])
                    if "volume" in fields:
                        try:
                            a["volume"] = max(0, min(100, int(fields["volume"])))
                        except (TypeError, ValueError):
                            pass
                    if "enabled" in fields:
                        a["enabled"] = bool(fields["enabled"])
                    # 仅一次：时间或模式变了才重新固定目标日期，避免编辑音量时把过期闹钟复活
                    if a.get("once") and (not old_once or a["time"] != old_time or not a.get("once_date")):
                        stamp_once_date(a)
                    save_alarms(self.alarms)
                    break
        self._sync_tasks()
        return self.get_alarms()

    def toggle_alarm(self, alarm_id):
        with self.lock:
            for a in self.alarms:
                if a["id"] == alarm_id:
                    a["enabled"] = not a["enabled"]
                    if a["enabled"] and a.get("once"):
                        # 重新开启一次性闹钟：按当前时间重新固定目标日期
                        stamp_once_date(a)
                    save_alarms(self.alarms)
                    break
        self._sync_tasks()
        return self.get_alarms()

    def delete_alarm(self, alarm_id):
        with self.lock:
            self.alarms = [a for a in self.alarms if a["id"] != alarm_id]
            save_alarms(self.alarms)
        self._sync_tasks()
        return self.get_alarms()

    def _sync_tasks(self):
        """闹钟增删改后，同步 Windows 计划任务（启用→注册，禁用/删除→移除）。"""
        try:
            sync_alarm_tasks(self.alarms)
        except Exception:
            logging.exception("[task] 同步计划任务失败")

    def test_notify(self):
        return show_toast("测试通知", "如果你看到了这条 Windows 通知，闹钟提醒功能就正常。",
                          silent=bool(self.settings.get("muted", True)))

    def get_ringtones(self):
        return get_ringtones()

    def upload_ringtone(self, filename, data_b64):
        try:
            return upload_ringtone(filename, data_b64)
        except Exception as exc:
            print("[api] upload_ringtone failed: %r" % (exc,))
            return get_ringtones()

    def delete_ringtone(self, name):
        return delete_ringtone(name)

    def play_ringtone(self, name, volume=70):
        return play_alarm_sound(name, volume=volume, muted=False)

    # ---------------- 后台到点检查 ----------------
    def run_checker(self):
        while not self._stop.is_set():
            now = current_time()
            stamp = now.strftime("%Y-%m-%d %H:%M")
            fired = load_fired()
            with self.lock:
                alarms = list(self.alarms)
            for a in alarms:
                if not a.get("enabled"):
                    continue
                # next_fire 返回的是“严格未来的时刻”，不能用来判断“是否已到点”；
                # 这里改用“当前是否落在 HH:MM 这一分钟内 + 今天是否匹配重复规则”。
                if not is_due_now(a, now, grace_minutes=1):
                    continue
                if self._last_fired.get(a["id"]) == stamp or fired.get(str(a["id"])) == stamp:
                    continue
                self._fire(a)
            self._stop.wait(1.0)

    def _fire(self, a):
        title = "闹钟时间到"
        body = "%s · %s（%s）" % (a["time"], a["label"], repeat_label(a))
        muted = bool(self.settings.get("muted", True))
        # 通知本身静音，由我们主动播放选中的铃声，便于支持自定义音频
        show_toast(title, body, silent=True)
        proc = start_alarm_sound(a.get("ringtone", "霓虹脉冲"), volume=a.get("volume", 70), muted=muted)
        self._start_ring(a, proc)
        stamp = current_time().strftime("%Y-%m-%d %H:%M")
        self._last_fired[a["id"]] = stamp
        # 同步写落到磁盘的已响记录，避免主程序退出瞬间与计划任务触发的无窗口进程重复响
        try:
            fired = load_fired()
            fired[str(a["id"])] = stamp
            save_fired(fired)
        except Exception:
            pass
        # 贪睡：开启声音且开启贪睡时，5 分钟后再提醒一次
        if a.get("snooze") and not muted:
            def snooze():
                time.sleep(300)
                show_toast(title, body + "（贪睡 5 分钟）", silent=True)
                proc2 = start_alarm_sound(a.get("ringtone", "霓虹脉冲"),
                                          volume=a.get("volume", 70), muted=False)
                self._start_ring(a, proc2)
            threading.Thread(target=snooze, daemon=True).start()
        # 仅一次：响过即停用，并移除对应的系统计划任务
        if a.get("once"):
            with self.lock:
                for it in self.alarms:
                    if it["id"] == a["id"]:
                        it["enabled"] = False
                        break
                save_alarms(self.alarms)
            self._sync_tasks()
        self._push_alarms()

    def _push_alarms(self):
        self._broadcast(
            "(function(){try{if(window.refreshAll)refreshAll();}catch(e){}"
            "try{if(window.onWidgetRefresh)onWidgetRefresh();}catch(e){}})()"
        )

    def _push_time_offset(self):
        """把校时偏移推给所有窗口（前端时钟用 Date.now()+offset 显示服务器时间）。"""
        try:
            off_ms = int(round(time_offset() * 1000))
        except Exception:
            off_ms = 0
        self._broadcast("try{if(window.setTimeOffset)window.setTimeOffset(%d);}catch(e){}" % off_ms)

    def _broadcast(self, js):
        """向所有窗口（主窗口 + 悬浮窗）推送 JS，窗口已关闭/隐藏时静默忽略。"""
        for w in list(getattr(webview, "windows", []) or []):
            try:
                w.evaluate_js(js)
            except Exception:
                pass

    # ---------------- 桌面悬浮窗 ----------------
    def _widget_cfg(self):
        cfg = self.settings.get("widget")
        return cfg if isinstance(cfg, dict) else {}

    def _save_widget_cfg(self, **kw):
        cfg = dict(self._widget_cfg())
        cfg.update(kw)
        self.settings["widget"] = cfg
        save_settings(self.settings)
        return cfg

    def _ring_payload(self):
        r = self._ring
        if not r:
            return None
        return {
            "id": r.get("id"),
            "label": r.get("label", ""),
            "until_ms": int(r.get("until", 0) * 1000),
        }

    def _start_ring(self, alarm, proc, seconds=RING_SECONDS):
        """进入响铃态（悬浮窗显示停止/贪睡），并起线程在结束或超时后自动复位。"""
        old = (self._ring or {}).get("proc")
        if old is not None:
            try:
                old.terminate()
            except Exception:
                pass
        self._ring_seq += 1
        seq = self._ring_seq
        self._ring = {
            "seq": seq,
            "id": alarm.get("id"),
            "label": alarm.get("label", ""),
            "until": time.time() + seconds,
            "proc": proc,
        }
        self._push_ring()

        def watch():
            deadline = time.time() + seconds
            while time.time() < deadline:
                if proc is not None and proc.poll() is not None:
                    break
                time.sleep(0.3)
            if (self._ring or {}).get("seq") == seq:
                self._ring = None
                self._push_ring()

        threading.Thread(target=watch, daemon=True).start()

    def _push_ring(self):
        payload = self._ring_payload()
        self._broadcast("window.onRing && window.onRing(%s)" % json.dumps(payload, ensure_ascii=False))

    def _stop_proc(self):
        proc = (self._ring or {}).get("proc")
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

    def stop_ringing(self):
        self._stop_proc()
        self._ring = None
        self._push_ring()
        return self.widget_state()

    def snooze_ringing(self, minutes=5):
        """响铃中贪睡：立即停止铃声，N 分钟后再次响起（并进入响铃态）。"""
        target = None
        rid = (self._ring or {}).get("id")
        for a in self.alarms:
            if a["id"] == rid:
                target = dict(a)
                break
        self._stop_proc()
        self._ring = None
        self._push_ring()
        if target is not None:
            def later():
                time.sleep(max(1, int(minutes)) * 60)
                show_toast("贪睡提醒", "%s · %s" % (target.get("time", ""), target.get("label", "")),
                           silent=True)
                proc = start_alarm_sound(target.get("ringtone", "霓虹脉冲"),
                                         volume=target.get("volume", 70),
                                         muted=bool(self.settings.get("muted", True)))
                self._start_ring(target, proc)
            threading.Thread(target=later, daemon=True).start()
        return self.widget_state()

    def widget_ring_state(self):
        return self.widget_state()

    def widget_state(self):
        """悬浮窗数据：折叠/置顶状态、已启用闹钟（含下次触发时间戳）、响铃态。"""
        now = current_time()
        items = []
        for a in sorted(self.alarms, key=lambda x: (x["time"], x["id"])):
            if not a.get("enabled"):
                continue
            nf = next_fire(a, now)
            items.append({
                "id": a["id"],
                "time": a["time"],
                "label": a["label"],
                "repeat": repeat_label(a),
                "next_ts": int(nf.timestamp()) if nf else 0,
            })
        cfg = self._widget_cfg()
        return {
            "collapsed": bool(cfg.get("collapsed", False)),
            "on_top": bool(cfg.get("on_top", True)),
            "alarms": items,
            "ringing": self._ring_payload(),
            "time_offset": round(time_offset(), 3),
        }

    def widget_toggle_collapse(self):
        cfg = self._widget_cfg()
        collapsed = not bool(cfg.get("collapsed", False))
        old_h = WIDGET_HEIGHT_COLLAPSED if cfg.get("collapsed") else WIDGET_HEIGHT
        new_h = WIDGET_HEIGHT_COLLAPSED if collapsed else WIDGET_HEIGHT
        self._save_widget_cfg(collapsed=collapsed)
        win = self._widget_win
        if win is not None:
            try:
                # 实时位置优先（用户可能刚拖动过），拿不到再回退配置
                try:
                    cur_x, cur_y = int(win.x), int(win.y)
                except Exception:
                    cur_x = int(cfg.get("x", 0))
                    cur_y = int(cfg.get("y", 0))
                win.resize(WIDGET_WIDTH, new_h)
                left, top, right, bottom = _monitor_work_area(
                    cur_x, cur_y, WIDGET_WIDTH, old_h)
                # 窗口中心在工作区下半部分 → 底边锚定：展开时向上长高、折叠时向下收起
                # 否则顶边锚定：展开时向下长高、折叠时向上收起
                anchored_bottom = (cur_y + old_h / 2) > (top + bottom) / 2
                new_y = cur_y + old_h - new_h if anchored_bottom else cur_y
                new_y = max(top, min(new_y, bottom - new_h))
                if (cur_x, new_y) != (cur_x, cur_y):
                    win.move(cur_x, new_y)
                self._save_widget_cfg(x=cur_x, y=new_y)
                logging.info("[widget] %s：高度 %d->%d，%s锚定，y %d->%d",
                             "折叠" if collapsed else "展开", old_h, new_h,
                             "底边" if anchored_bottom else "顶边", cur_y, new_y)
            except Exception:
                logging.exception("[widget] 调整尺寸失败")
        return self.widget_state()

    def widget_toggle_on_top(self):
        on_top = not bool(self._widget_cfg().get("on_top", True))
        self._save_widget_cfg(on_top=on_top)
        win = self._widget_win
        if win is not None:
            try:
                win.on_top = on_top
            except Exception:
                logging.exception("[widget] 切换置顶失败")
            # 双保险：Win32 直接设一次，确保切换立即生效
            _set_widget_topmost(win, on_top)
            logging.info("[widget] 置顶 -> %s", "开" if on_top else "关")
        return self.widget_state()

    def widget_hover(self, inside):
        """悬停时顶部图标由 35% 提升到 100%（纯前端处理，这里保留接口便于扩展）。"""
        return bool(inside)

    def widget_show(self):
        self._save_widget_cfg(visible=True)
        if self._widget_win is not None:
            try:
                self._widget_win.show()
            except Exception:
                pass
        return {"visible": True}

    def widget_hide(self):
        self._save_widget_cfg(visible=False)
        if self._widget_win is not None:
            try:
                self._widget_win.hide()
            except Exception:
                pass
        # 通知主窗口同步「悬浮窗」开关状态
        self._broadcast("window.onWidgetVisibility && window.onWidgetVisibility(false)")
        return {"visible": False}

    def open_main_window(self):
        try:
            if self._main_win is not None:
                self._main_win.show()
                self._main_win.restore()
                self._main_win.on_top = True
                self._main_win.on_top = False
        except Exception:
            logging.exception("[widget] 打开主窗口失败")
        return True

    def open_main_alarm(self, alarm_id):
        self.open_main_window()
        try:
            if self._main_win is not None:
                self._main_win.evaluate_js("window.focusAlarm && focusAlarm(%d)" % int(alarm_id))
        except Exception:
            pass
        return True


class WidgetAPI:
    """悬浮窗专用 js_api。

    pywebview 对同一个 js_api 对象只会导出一次方法（第二个窗口拿到空列表），
    因此悬浮窗必须有独立实例，内部统一委托给主 ChronosAPI。
    """

    def __init__(self, api):
        self._api = api

    def widget_state(self):
        return self._api.widget_state()

    def widget_ring_state(self):
        return self._api.widget_ring_state()

    def widget_toggle_collapse(self):
        return self._api.widget_toggle_collapse()

    def widget_toggle_on_top(self):
        return self._api.widget_toggle_on_top()

    def widget_hover(self, inside):
        return self._api.widget_hover(inside)

    def widget_hide(self):
        return self._api.widget_hide()

    def widget_show(self):
        return self._api.widget_show()

    def stop_ringing(self):
        return self._api.stop_ringing()

    def snooze_ringing(self):
        return self._api.snooze_ringing()

    def open_main_window(self):
        return self._api.open_main_window()

    def open_main_alarm(self, alarm_id):
        return self._api.open_main_alarm(alarm_id)


def setup_tray(window, api):
    """创建系统托盘：点击关闭窗口时最小化到托盘而非退出；托盘菜单提供「显示窗口 / 退出」。

    pywebview 在独立 STA 线程运行窗口消息循环，本函数在另一个 STA 托盘线程运行
    NotifyIcon 的消息循环（每线程独立的 Application.Run）。窗口操作经 pywebview 的
    Invoke 转发到窗口线程，避免跨线程访问 WinForms 控件。动态绘制图标，无需外部 .ico。
    若托盘初始化失败则安全降级为普通关闭行为。
    """
    logging.info("[tray] setup_tray started")
    try:
        import clr
        try:
            clr.AddReference("System.Windows.Forms")
            clr.AddReference("System.Drawing")
        except Exception:
            pass
        from System.Windows.Forms import (NotifyIcon, ContextMenuStrip, ToolStripMenuItem,
                                          ToolTipIcon, Application, ApplicationContext)
        from System.Drawing import (Bitmap, Graphics, Color, SolidBrush, Font,
                                     StringFormat, RectangleF, Icon, StringAlignment, FontStyle)
        from System.Drawing.Drawing2D import SmoothingMode
        from System.Threading import Thread, ApartmentState, ThreadStart

        ni = [None]
        ctx = ApplicationContext()
        _tray_refs = []  # 保留 GDI 对象引用，防止 GC 回收导致托盘图标消失

        def build_tray():
            # 在 STA 托盘线程中创建 NotifyIcon 并运行消息循环
            if ICON_PATH and os.path.exists(ICON_PATH):
                try:
                    icon = Icon(ICON_PATH)
                    logging.info("[tray] loaded icon from %s", ICON_PATH)
                except Exception:
                    logging.exception("[tray] failed to load icon, fallback to drawing")
                    icon = None
            else:
                icon = None

            if icon is None:
                # 回退：动态绘制深蓝「C」
                size = 32
                bmp = Bitmap(size, size)
                g = Graphics.FromImage(bmp)
                g.SmoothingMode = SmoothingMode.AntiAlias
                g.Clear(Color.Transparent)
                g.FillEllipse(SolidBrush(Color.FromArgb(0, 212, 255)), 1, 1, size - 2, size - 2)
                sf = StringFormat()
                sf.Alignment = StringAlignment.Center
                sf.LineAlignment = StringAlignment.Center
                g.DrawString("C", Font("Segoe UI", 18, FontStyle.Bold),
                             SolidBrush(Color.FromArgb(4, 18, 46)), RectangleF(0, 0, size, size), sf)
                icon = Icon.FromHandle(bmp.GetHicon())
                _tray_refs.extend([bmp, g, icon])

            ic = NotifyIcon()
            ic.Icon = icon
            ic.Text = "Chronos 闹钟"
            ic.Visible = True
            ni[0] = ic
            try:
                ic.ShowBalloonTip(4000, "Chronos 闹钟",
                                  "已启动。点击窗口关闭按钮会最小化到托盘，闹钟仍会正常提醒。",
                                  ToolTipIcon.Info)
            except Exception:
                pass

            menu = ContextMenuStrip()
            item_show = ToolStripMenuItem("显示窗口")
            item_quit = ToolStripMenuItem("退出")
            menu.Items.Add(item_show)
            menu.Items.Add(item_quit)
            ic.ContextMenuStrip = menu

            def on_show(sender, args):
                api._quitting = False
                try:
                    window.show()
                    window.restore()
                except Exception:
                    pass

            def on_quit(sender, args):
                api._quitting = True
                try:
                    ic.Visible = False
                    ic.Dispose()
                except Exception:
                    pass
                try:
                    ctx.ExitThread()
                except Exception:
                    pass
                try:
                    window.destroy()
                except Exception:
                    pass
                try:
                    if getattr(api, "_widget_win", None) is not None:
                        api._widget_win.destroy()
                except Exception:
                    pass

            item_show.Click += on_show
            item_quit.Click += on_quit
            ic.DoubleClick += on_show
            logging.info("[tray] NotifyIcon created and message loop running")
            Application.Run(ctx)

        # 关闭拦截：pywebview 的 closing 事件调用时不传参；若函数签有 window 参数，
        # pywebview 会传入 pywebview Window 实例。返回 False 即通知 pywebview 取消关闭。
        def on_closing(window=None):
            logging.info("[tray] on_closing fired, _quitting=%s", getattr(api, "_quitting", False))
            if getattr(api, "_quitting", False):
                return True
            try:
                if window is not None:
                    window.hide()
                    logging.info("[tray] window hidden -> minimize to tray")
                else:
                    logging.warning("[tray] window is None, cannot hide")
            except Exception:
                logging.exception("[tray] window.hide() failed")
            return False
        try:
            window.events.closing += on_closing
            logging.info("[tray] closing event registered")
        except Exception:
            logging.exception("[tray] failed to register closing event")

        t = Thread(ThreadStart(build_tray))
        t.SetApartmentState(ApartmentState.STA)
        t.IsBackground = False
        t.Start()
        return t
    except Exception as exc:
        print("[tray] 系统托盘初始化失败，将使用普通关闭行为: %r" % (exc,))
        return None


def _already_running():
    msg = ("<html style='font-family:system-ui;background:#0a1430;color:#c3d0f0;"
           "display:flex;height:100vh;align-items:center;justify-content:center'>"
           "<div style='text-align:center'><div style='font-size:22px;color:#00d4ff'>Chronos 闹钟</div>"
           "<p style='margin-top:12px'>闹钟程序已经在运行中了。</p></div></html>")
    webview.create_window("Chronos 闹钟", html=msg, width=360, height=160)
    webview.start(gui="edgechromium")


def fire_alarm_headless(alarm_id):
    """由系统计划任务触发的无窗口静默模式：仅当主程序未运行时才真正提醒，避免重复响。

    与主程序共用单实例锁：若锁已被主程序持有（程序正在运行），直接退出，交给它的检查线程；
    否则判断是否“此刻该响”（is_due_now + 已响记录跨进程去重），该响则弹通知+铃声后退出。
    """
    lock = acquire_single_instance()
    if lock is None:
        logging.info("[alarm] 主程序正在运行，跳过（由检查线程负责响铃）")
        return 0
    try:
        try:
            aid = int(alarm_id)
        except (TypeError, ValueError):
            return 1
        now = current_time()
        stamp = now.strftime("%Y-%m-%d %H:%M")
        fired = load_fired()
        if fired.get(str(aid)) == stamp:
            logging.info("[alarm] 闹钟 id=%d 本分钟已响过，跳过", aid)
            return 0
        alarms = load_alarms()
        a = next((x for x in alarms if x["id"] == aid), None)
        if a and is_due_now(a, now):
            settings = load_settings()
            muted = bool(settings.get("muted", True))
            show_toast("闹钟", a.get("label") or "时间到了", silent=muted)
            if not muted:
                play_alarm_sound(a.get("ringtone", "霓虹脉冲"), volume=a.get("volume", 70), muted=False)
            fired[str(aid)] = stamp
            save_fired(fired)
            # 仅一次：响过即停用，并同步移除此闹钟的系统任务
            if a.get("once"):
                a["enabled"] = False
                save_alarms(alarms)
                try:
                    sync_alarm_tasks(alarms)
                except Exception:
                    logging.exception("[task] 同步失败")
            logging.info("[alarm] 已触发闹钟 id=%d（静默模式）", aid)
        else:
            logging.info("[alarm] 闹钟 id=%d 此刻不该响，跳过", aid)
        return 0
    finally:
        try:
            lock.close()
        except Exception:
            pass


def _work_area():
    """主屏工作区（排除任务栏），返回 (left, top, right, bottom)。"""
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0):
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return 0, 0, 1920, 1080


def _cursor_screen():
    """光标所在的显示器（多屏时把悬浮窗放到用户正在用的那块屏）。"""
    try:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        for s in webview.screens:
            if s.x <= pt.x < s.x + s.width and s.y <= pt.y < s.y + s.height:
                return s
    except Exception:
        pass
    screens = webview.screens
    return screens[0] if screens else None


def _on_any_screen(x, y, height):
    """坐标是否落在任一显示器可用范围内（防止显示器变更后窗口跑到屏幕外）。"""
    try:
        for s in webview.screens:
            if (s.x - WIDGET_WIDTH + 80) <= x <= (s.x + s.width - 80) and \
               (s.y - height + 40) <= y <= (s.y + s.height - 50):
                return True
    except Exception:
        return True
    return False


def widget_position(api):
    """悬浮窗初始位置：优先用上次保存的位置，否则贴当前屏右下角（避开任务栏）。"""
    cfg = api._widget_cfg()
    height = WIDGET_HEIGHT_COLLAPSED if cfg.get("collapsed") else WIDGET_HEIGHT
    try:
        x, y = int(cfg["x"]), int(cfg["y"])
        if _on_any_screen(x, y, height):
            return x, y
    except Exception:
        pass

    s = _cursor_screen()
    if s is None:
        return None, None
    # 主屏优先用系统工作区（精确排除任务栏）
    left, top, right, bottom = _work_area()
    if s.x == 0 and s.y == 0 and right > 0:
        return right - WIDGET_WIDTH - WIDGET_MARGIN, bottom - height - WIDGET_MARGIN
    return (s.x + s.width - WIDGET_WIDTH - WIDGET_MARGIN,
            s.y + s.height - height - 64)


def _round_widget_corners(win):
    """Windows 11：给无边框窗口加圆角（22px 视觉半径）。旧系统静默忽略。"""
    try:
        import ctypes
        hwnd = int(win.native.Handle.ToInt64())
    except Exception:
        return
    try:
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_int(DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(ctypes.c_int(DWMWCP_ROUND)),
            ctypes.sizeof(ctypes.c_int),
        )
    except Exception:
        pass


SNAP_EDGE_DISTANCE = 24  # 距离屏幕工作区边缘多少像素内自动吸附


def _monitor_work_area(x, y, w, h):
    """返回矩形中心所在显示器的工作区 (left, top, right, bottom)，逻辑坐标。

    用 pywebview 的 screens（已是逻辑坐标）而不是 GetMonitorInfo：
    进程 DPI 感知时后者返回物理像素，与 window.x/y 不在同一坐标系会算错吸附方向。
    任务栏高度只在主屏底部扣减（用物理解析度算出纵向比例再换算）。
    """
    try:
        screens = list(webview.screens or [])
    except Exception:
        screens = []
    if not screens:
        return (0, 0, 1920, 1080)

    cx, cy = x + w // 2, y + h // 2
    target = None
    # 选与窗口矩形「重叠面积最大」的显示器：
    # 只看中心点会在窗口贴边/跨屏时判错（例如贴底展开时中心点跑出屏幕外）
    best_area = 0
    for s in screens:
        ox = min(x + w, s.x + s.width) - max(x, s.x)
        oy = min(y + h, s.y + s.height) - max(y, s.y)
        if ox > 0 and oy > 0:
            area = ox * oy
            if area > best_area:
                best_area = area
                target = s
    if target is None:
        # 完全落在所有屏幕之外：退化为最近中心点的显示器
        target = min(screens, key=lambda s: (s.x + s.width / 2 - cx) ** 2 + (s.y + s.height / 2 - cy) ** 2)

    left, top = target.x, target.y
    right, bottom = target.x + target.width, target.y + target.height

    # 主屏底部有任务栏（副屏一般没有）
    if target.x == 0 and target.y == 0:
        try:
            import ctypes
            from ctypes import wintypes

            u = ctypes.windll.user32
            wa = wintypes.RECT()
            u.SystemParametersInfoW(48, 0, ctypes.byref(wa), 0)  # 物理工作区
            mon_h = u.GetSystemMetrics(1)                        # 物理屏高
            if mon_h > 0 and wa.bottom > 0:
                bottom = top + int(round(target.height * (wa.bottom - wa.top) / mon_h))
        except Exception:
            bottom -= 48
    return left, top, right, bottom


def _snap_widget_position(x, y, w, h):
    """靠近工作区边缘（<=24px）时贴边，返回吸附后的坐标。"""
    left, top, right, bottom = _monitor_work_area(x, y, w, h)
    nx, ny = x, y
    if abs(x - left) <= SNAP_EDGE_DISTANCE:
        nx = left
    elif abs((x + w) - right) <= SNAP_EDGE_DISTANCE:
        nx = right - w
    if abs(y - top) <= SNAP_EDGE_DISTANCE:
        ny = top
    elif abs((y + h) - bottom) <= SNAP_EDGE_DISTANCE:
        ny = bottom - h
    # 防止窗口整体跑出工作区
    nx = max(left - 0, min(nx, right - w))
    ny = max(top, min(ny, bottom - h))
    return nx, ny


def _set_widget_topmost(win, on_top):
    """用 Win32 明确设置/取消置顶（比 WinForms 的 TopMost 更可控，且不激活窗口）。"""
    try:
        import ctypes
        from ctypes import wintypes

        # 注意：必须用 HWND(-1/-2) 包装，直接传 int -1 会被截成 0xFFFFFFFF 而失效
        HWND_TOPMOST, HWND_NOTOPMOST = wintypes.HWND(-1), wintypes.HWND(-2)
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        hwnd = wintypes.HWND(int(win.native.Handle.ToInt64()))
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST if on_top else HWND_NOTOPMOST,
            0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        return True
    except Exception:
        logging.exception("[widget] 设置置顶失败")
        return False


def _widget_topmost_guard(api):
    """置顶保持：其它置顶窗口（播放器、聊天工具等）会盖住悬浮窗，定期重新提到最上层。

    只在配置 on_top=True 且窗口可见时执行，且不抢焦点（SWP_NOACTIVATE）。
    """
    import ctypes
    from ctypes import wintypes

    HWND_TOPMOST = wintypes.HWND(-1)
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010

    while not api._stop.wait(2.0):
        cfg = api._widget_cfg()
        if not cfg.get("on_top", True) or not cfg.get("visible", True):
            continue
        win = api._widget_win
        if win is None:
            continue
        try:
            hwnd = wintypes.HWND(int(win.native.Handle.ToInt64()))
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        except Exception:
            pass


def _hide_widget_from_taskbar(win):
    """把悬浮窗标记为辅助窗口：不占任务栏按钮、也不出现在 Alt+Tab。

    只用 Win32 的 WS_EX_TOOLWINDOW：WinForms 的 ShowInTaskbar=False 会重建窗口句柄，
    把 WebView2 宿主一起弄丢（实测窗口直接消失），所以不走那条路。
    """
    try:
        import ctypes
        from ctypes import wintypes

        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x20

        hwnd = wintypes.HWND(int(win.native.Handle.ToInt64()))
        u = ctypes.windll.user32
        ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        new_ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        if new_ex == ex:
            logging.info("[widget] 任务栏样式已是辅助窗口，无需处理")
            return
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, new_ex)
        # FRAMECHANGED 让系统重新评估窗口的可见性归属（任务栏按钮/Alt+Tab）
        u.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                       SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED)
        logging.info("[widget] 已从任务栏隐藏（WS_EX_TOOLWINDOW, ex 0x%X -> 0x%X）",
                     ex & 0xFFFFFFFF, new_ex & 0xFFFFFFFF)
    except Exception:
        logging.exception("[widget] 任务栏隐藏失败")


def _bind_widget_events(api, widget_win):
    """拖动结束保存位置（防抖），窗口显示时补一次圆角。"""
    state = {"timer": None}

    def save_pos():
        try:
            x, y = widget_win.x, widget_win.y
            if x is None or y is None:
                return
            x, y = int(x), int(y)
            cfg = api._widget_cfg()
            h = WIDGET_HEIGHT_COLLAPSED if cfg.get("collapsed") else WIDGET_HEIGHT
            nx, ny = _snap_widget_position(x, y, WIDGET_WIDTH, h)
            if (nx, ny) != (x, y):
                widget_win.move(nx, ny)
                logging.info("[widget] 吸附边缘: (%d,%d) -> (%d,%d)", x, y, nx, ny)
                x, y = nx, ny
            api._save_widget_cfg(x=x, y=y)
        except Exception:
            logging.exception("[widget] 保存位置失败")

    def on_moved():
        if state["timer"] is not None:
            try:
                state["timer"].cancel()
            except Exception:
                pass
        t = threading.Timer(0.4, save_pos)
        t.daemon = True
        state["timer"] = t
        t.start()

    def on_shown():
        _round_widget_corners(widget_win)
        # 悬浮窗只做桌面挂件，不占任务栏按钮 / 不进 Alt+Tab
        if not state.get("taskbar_hidden"):
            state["taskbar_hidden"] = True
            _hide_widget_from_taskbar(widget_win)
        # pywebview 在启用 resizable=False(FixedSingle) 时按外框设尺寸，无边框化后客户区会小一圈，
        # 这里按目标客户区尺寸再校正一次，保证 340×376 的 CSS 视口与设计稿一致。
        try:
            cfg = api._widget_cfg()
            h = WIDGET_HEIGHT_COLLAPSED if cfg.get("collapsed") else WIDGET_HEIGHT
            widget_win.resize(WIDGET_WIDTH, h)
        except Exception:
            logging.exception("[widget] 校正尺寸失败")
        # 启动时若配置为「不显示」，等首次渲染完成再隐藏（见 create_window 处说明）
        if not api._widget_cfg().get("visible", True):
            try:
                widget_win.hide()
                logging.info("[widget] 按配置隐藏悬浮窗（WebView2 已完成初始化）")
            except Exception:
                logging.exception("[widget] 隐藏悬浮窗失败")
        # 重新显示后恢复置顶（hide/show 可能丢失 topmost 状态）
        _set_widget_topmost(widget_win, bool(api._widget_cfg().get("on_top", True)))

    try:
        widget_win.events.moved += on_moved
    except Exception:
        logging.exception("[widget] moved 事件注册失败")
    try:
        widget_win.events.shown += on_shown
    except Exception:
        pass


def _apply_main_icon(title="Chronos 闹钟"):
    """用纯 Win32 给主窗口换图标（标题栏、Alt-Tab、任务栏按钮）。

    pywebview 的 WinForms 窗口不会自动读 exe 里嵌的资源图标，不显式设的话标题栏
    会显示 .NET 的默认图标，和任务栏/快捷方式上的新图标对不上。

    这里故意不用 pythonnet 的 `window.native.Icon`：托盘线程已经在用 CLR，再开一个
    线程同时 add_reference / 加载程序集会互相卡死（实测整个 GUI 起不来）；
    而按窗口标题 FindWindow + WM_SETICON 完全走 user32，没有这个问题。
    """
    if not (ICON_PATH and os.path.exists(ICON_PATH)):
        return

    def worker():
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.SendMessageW.restype = wintypes.LPARAM
        u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
        WM_SETICON, WM_GETICON, ICON_SMALL, ICON_BIG = 0x0080, 0x007F, 0, 1

        big = u.LoadImageW(None, ICON_PATH, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        small = u.LoadImageW(None, ICON_PATH, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        if not big:
            logging.warning("[ui] LoadImage 失败（错误码 %s），未设置图标",
                            ctypes.windll.kernel32.GetLastError())
            return

        # 窗口建出来后 WinForms 还会再刷一次自己的默认图标，所以这里设完要读回来校验，
        # 被盖回去就再设，最多试 6 秒。HICON 不销毁——窗口会一直持有。
        for _ in range(20):
            hwnd = u.FindWindowW(None, title)
            if hwnd:
                u.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
                if small:
                    u.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
                if u.SendMessageW(hwnd, WM_GETICON, ICON_BIG, 0) == big:
                    logging.info("[ui] 已设置窗口图标 %s", ICON_PATH)
                    return
            time.sleep(0.3)
        logging.warning("[ui] 试了 6 秒仍未把图标挂上窗口 %r", title)

    threading.Thread(target=worker, daemon=True).start()


TIME_SYSTEM_SHIFT = 5.0  # 系统时间偏差超过该值才提权同步（平时不打扰）


def _try_elevate_system_time(api):
    """以管理员身份启动一个只做校时的子进程（会弹一次 UAC）。

    普通权限下 SetLocalTime 一定失败（错误 1314），所以真正改系统时间只能走提权子进程。
    """
    import ctypes

    exe = sys.executable
    if getattr(sys, "frozen", False):
        params = "--sync-system-time"
    else:
        # 开发模式：python.exe + main.py 路径
        params = '"%s" --sync-system-time' % os.path.abspath(sys.argv[0])
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 0)
    except Exception:
        logging.exception("[time] 请求提权失败")
        return False

    if ret > 32:
        logging.info("[time] 已请求管理员权限同步系统时间，等待子进程完成")
        time.sleep(2.5)
        st = sync_time(apply_system_clock=False)
        logging.info("[time] 提权后重新校时：偏差 %+.2fs", st.get("raw_offset") or 0.0)
        if api is not None:
            try:
                api._push_alarms()
            except Exception:
                pass
        return True

    logging.warning("[time] 提权被拒绝或失败（ShellExecute 返回 %s），系统时间未同步", ret)
    return False


def _start_time_sync(api):
    """启动即向 NTP 服务器校时（开机自启同样生效），之后每 TIME_SYNC_INTERVAL 复查。"""

    state = {"elevated_tried": False}

    def worker():
        time.sleep(1.5)  # 等窗口就绪，便于把校时结果推到界面
        while True:
            try:
                st = sync_time(apply_system_clock=True)
                if st.get("synced"):
                    logging.info("[time] 校时成功：%s 偏差 %+.2fs（系统时间%s）",
                                 st.get("server"), st.get("raw_offset") or 0.0,
                                 "已同步" if st.get("system_time_set") else "未改动")
                    raw = abs(st.get("raw_offset") or 0.0)
                    # 偏差明显且没权限改系统时间时，问一次用户要不要提权同步
                    if (not state["elevated_tried"]
                            and not st.get("system_time_set")
                            and raw >= TIME_SYSTEM_SHIFT):
                        state["elevated_tried"] = True
                        _try_elevate_system_time(api)
                    api._push_time_offset()
                    api._push_alarms()
                else:
                    logging.warning("[time] 校时失败：%s", st.get("error"))
            except Exception:
                logging.exception("[time] 校时线程异常")
            if api._stop.wait(TIME_SYNC_INTERVAL):
                break

    threading.Thread(target=worker, name="time-sync", daemon=True).start()


def main(argv):
    # --sync-system-time：以管理员身份运行的子进程，只负责把系统时间改成服务器时间
    if "--sync-system-time" in argv:
        st = sync_time(apply_system_clock=False)  # 只取服务器时间，不动系统时间
        if not st.get("synced"):
            logging.warning("[time] 提权同步失败：无法获取服务器时间（%s）", st.get("error"))
            return 1
        target = current_time()
        ok = set_system_time(target)
        logging.info("[time] 提权同步系统时间 -> %s：%s",
                     target.strftime("%Y-%m-%d %H:%M:%S"), "成功" if ok else "失败")
        return 0 if ok else 1

    if "--selftest" in argv:
        return selftest()
    if "--test-notify" in argv:
        return 0 if show_toast("测试通知", "如果你看到了这条 Windows 通知，闹钟提醒功能就正常。") else 1

    # --alarm <id>：由 Windows 计划任务触发，无窗口静默提醒
    if "--alarm" in argv:
        try:
            idx = argv.index("--alarm")
            return fire_alarm_headless(argv[idx + 1])
        except (IndexError, ValueError):
            return 1

    # --hidden：开机自启等场景使用，窗口初始隐藏，仅驻留系统托盘
    start_hidden = "--hidden" in argv
    # --no-widget：不显示桌面悬浮窗（排障用）
    want_widget = "--no-widget" not in argv

    if not os.path.exists(HTML_PATH):
        print("[chronos] 找不到 ui/index.html，请确认目录结构。")
        return 1

    lock = acquire_single_instance()
    if lock is None:
        _already_running()
        return 0

    api = ChronosAPI()
    threading.Thread(target=api.run_checker, daemon=True).start()

    # 启动时对系统计划任务做一次对账，确保与当前启用的闹钟一致
    try:
        sync_alarm_tasks(api.alarms)
    except Exception:
        logging.exception("[task] 启动对账失败")

    url = Path(HTML_PATH).as_uri()
    window = webview.create_window(
        "Chronos 闹钟",
        url=url,
        js_api=api,
        width=1100,
        height=720,
        min_size=(920, 600),
        hidden=start_hidden,
    )
    api._main_win = window

    # 桌面悬浮窗（当前时间 + 已开启的闹钟，常驻桌面右下角）
    if want_widget and os.path.exists(WIDGET_HTML_PATH):
        cfg = api._widget_cfg()
        collapsed = bool(cfg.get("collapsed", False))
        visible = bool(cfg.get("visible", True))
        wx, wy = widget_position(api)
        try:
            widget_win = webview.create_window(
                "Chronos 悬浮窗",
                url=Path(WIDGET_HTML_PATH).as_uri(),
                js_api=WidgetAPI(api),
                width=WIDGET_WIDTH,
                height=WIDGET_HEIGHT_COLLAPSED if collapsed else WIDGET_HEIGHT,
                x=wx, y=wy,
                frameless=True,
                easy_drag=False,
                resizable=False,
                min_size=(WIDGET_WIDTH, WIDGET_HEIGHT_COLLAPSED),
                on_top=bool(cfg.get("on_top", True)),
                background_color="#0A1430",
                # 不能 hidden=True 创建：隐藏创建的 WebView2 往往不初始化，
                # 之后 show 出来就是一块空白板。改为可见创建，首次显示后再按配置隐藏。
                hidden=False,
                text_select=False,
                zoomable=False,
            )
            api._widget_win = widget_win
            if widget_win is not None:
                _bind_widget_events(api, widget_win)
        except Exception:
            logging.exception("[widget] 创建悬浮窗失败，已跳过")

    # 系统托盘：关闭窗口改为最小化到托盘
    setup_tray(window, api)
    # 启动即校准服务器时间（开机自启同样生效）
    _start_time_sync(api)
    # 悬浮窗置顶保持（防止被其它置顶窗口盖住）
    if api._widget_win is not None:
        threading.Thread(target=_widget_topmost_guard, args=(api,),
                         name="widget-topmost", daemon=True).start()
    # 主窗口图标（标题栏/Alt-Tab）：内部起线程轮询等窗口出现，不阻塞启动
    _apply_main_icon(getattr(window, "title", None) or "Chronos 闹钟")

    def _after_gui_start():
        """GUI 起来后把悬浮窗从任务栏摘掉（此时 native 窗口才真正就绪）。"""
        win = api._widget_win
        if win is None:
            return
        for _ in range(40):
            try:
                if win.native is not None:
                    _hide_widget_from_taskbar(win)
                    return
            except Exception:
                pass
            time.sleep(0.25)
        logging.warning("[widget] 等待窗口就绪超时，未能从任务栏隐藏")

    webview.start(gui="edgechromium", func=_after_gui_start)
    api._stop.set()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        import traceback
        try:
            with open(os.path.join(APP_DIR, "error.log"), "a", encoding="utf-8") as fh:
                fh.write("\n===== %s =====\n%s\n" % (datetime.now(), traceback.format_exc()))
        except Exception:
            pass
        raise
