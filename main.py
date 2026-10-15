# -*- coding: utf-8 -*-
"""Chronos 闹钟 —— pywebview 桌面外壳。

前端（ui/index.html + app.js + style.css）实现资料库 Chronos 深蓝科技风设计；
本文件提供原生窗口、Python 后端 API（闹钟存取 / 通知 / 设置）与后台到点检查线程。
"""
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
    get_autostart, set_autostart, sync_alarm_tasks,
    load_fired, save_fired,
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
ICON_PATH = os.path.join(BUNDLE_DIR, "icon", "appicon.ico")
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

    # ---------------- 供前端调用 ----------------
    def get_alarms(self):
        now = datetime.now()
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
        now = datetime.now()
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
        }

    def set_muted(self, muted):
        self.settings["muted"] = bool(muted)
        save_settings(self.settings)
        return self.get_settings()

    def set_autostart(self, enabled):
        ok = set_autostart(bool(enabled))
        return {"autostart": get_autostart(), "ok": ok}

    def add_alarm(self, time, label, days, ringtone="霓虹脉冲", snooze=False, volume=70, china_workday=False):
        try:
            datetime.strptime(time, "%H:%M")
        except Exception:
            return self.get_alarms()
        days = [int(d) for d in (days or []) if 0 <= int(d) <= 6]
        china_workday = bool(china_workday)
        if not days and not china_workday:
            return self.get_alarms()
        if china_workday and not days:
            days = [0, 1, 2, 3, 4]
        with self.lock:
            self.alarms.append({
                "id": self._next_id,
                "time": time,
                "label": str(label or "闹钟"),
                "enabled": True,
                "days": days,
                "once": False,
                "china_workday": china_workday,
                "ringtone": str(ringtone or "霓虹脉冲"),
                "snooze": bool(snooze),
                "volume": int(volume or 70),
            })
            self._next_id += 1
            save_alarms(self.alarms)
        self._sync_tasks()
        return self.get_alarms()

    def update_alarm(self, alarm_id, fields):
        fields = fields or {}
        with self.lock:
            for a in self.alarms:
                if a["id"] == alarm_id:
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
                    if "ringtone" in fields:
                        a["ringtone"] = str(fields["ringtone"])
                    if "snooze" in fields:
                        a["snooze"] = bool(fields["snooze"])
                    if "volume" in fields:
                        a["volume"] = int(fields["volume"] or 70)
                    if "enabled" in fields:
                        a["enabled"] = bool(fields["enabled"])
                    save_alarms(self.alarms)
                    break
        self._sync_tasks()
        return self.get_alarms()

    def toggle_alarm(self, alarm_id):
        with self.lock:
            for a in self.alarms:
                if a["id"] == alarm_id:
                    a["enabled"] = not a["enabled"]
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
            now = datetime.now()
            stamp = now.strftime("%Y-%m-%d %H:%M")
            fired = load_fired()
            with self.lock:
                alarms = list(self.alarms)
            for a in alarms:
                if not a["enabled"]:
                    continue
                nf = next_fire(a, now)
                if nf is None or nf > now:
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
        if not muted:
            play_alarm_sound(a.get("ringtone", "霓虹脉冲"), volume=a.get("volume", 70), muted=False)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
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
                play_alarm_sound(a.get("ringtone", "霓虹脉冲"), volume=a.get("volume", 70), muted=False)
            threading.Thread(target=snooze, daemon=True).start()
        # 仅一次：响过即停用
        if a.get("once"):
            with self.lock:
                for it in self.alarms:
                    if it["id"] == a["id"]:
                        it["enabled"] = False
                        break
                save_alarms(self.alarms)
        self._push_alarms()

    def _push_alarms(self):
        try:
            win = webview.windows[0]
            win.evaluate_js("refreshAll()")
        except Exception:
            pass


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
        now = datetime.now()
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


def main(argv):
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
    # 系统托盘：关闭窗口改为最小化到托盘
    setup_tray(window, api)
    webview.start(gui="edgechromium")
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
