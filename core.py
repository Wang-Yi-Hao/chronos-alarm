# -*- coding: utf-8 -*-
"""Chronos 闹钟 —— 核心逻辑（无 GUI 依赖，可独立测试）。

时间计算、存取、设置、Windows 通知。UI 层（main.py / app.js）调用这里的函数。
闹钟数据保存在同目录 alarms.json，设置保存在 settings.json。
首次运行会从上级目录（旧版 Windows闹钟）迁移已有数据。
"""
import base64
import json
import math
import os
import shutil
import socket
import subprocess
import struct
import sys
import threading
import time
import wave
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = APP_DIR
DATA_FILE = os.path.join(DATA_DIR, "alarms.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
RINGTONES_DIR = os.path.join(DATA_DIR, "ringtones")
PRESET_RINGTONES = ["霓虹脉冲", "深海回响", "星轨", "无声震动"]
PRESET_FILENAMES = {p + ".wav" for p in PRESET_RINGTONES}

WEEKDAY_CN = "一二三四五六日"
# 旧版 repeat 值 → 星期集合（0=周一 … 6=周日）
_LEGACY_DAYS = {
    "daily": [0, 1, 2, 3, 4, 5, 6],
    "weekdays": [0, 1, 2, 3, 4],
    "weekend": [5, 6],
}

# 中国法定节假日 / 调休上班数据（国务院公布）。
# 年份未覆盖时回退为「周一到周五」。用户可把同名 holidays.json 放到数据目录覆盖/扩展。
_CHINA_HOLIDAYS_BUILTIN = {
    2025: {
        "off": ["2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30",
                "2025-01-31", "2025-02-01", "2025-02-02", "2025-02-03",
                "2025-02-04", "2025-04-04", "2025-04-05", "2025-04-06",
                "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04",
                "2025-05-05", "2025-05-31", "2025-06-01", "2025-06-02",
                "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-04",
                "2025-10-05", "2025-10-06", "2025-10-07", "2025-10-08"],
        "work": ["2025-01-26", "2025-02-08", "2025-04-27", "2025-09-28", "2025-10-11"],
    },
    2026: {
        "off": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-02-15",
                "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
                "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
                "2026-04-04", "2026-04-05", "2026-04-06", "2026-05-01",
                "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
                "2026-06-19", "2026-06-20", "2026-06-21", "2026-09-25",
                "2026-09-26", "2026-09-27", "2026-10-01", "2026-10-02",
                "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07"],
        "work": ["2026-01-04", "2026-02-14", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-10"],
    },
}

_china_holidays_cache = None


def _parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def load_china_holidays():
    """返回 {year: {'off': set(date), 'work': set(date)}}。"""
    global _china_holidays_cache
    if _china_holidays_cache is not None:
        return _china_holidays_cache
    result = {}
    for year, v in _CHINA_HOLIDAYS_BUILTIN.items():
        result[year] = {
            "off": set(_parse_date(d) for d in v["off"]),
            "work": set(_parse_date(d) for d in v["work"]),
        }
    override_path = os.path.join(DATA_DIR, "holidays.json")
    if os.path.exists(override_path):
        try:
            with open(override_path, "r", encoding="utf-8") as fh:
                override = json.load(fh)
            for year_key, v in override.items():
                year = int(year_key)
                result[year] = {
                    "off": set(_parse_date(d) for d in v.get("off", [])),
                    "work": set(_parse_date(d) for d in v.get("work", [])),
                }
        except Exception:
            pass
    _china_holidays_cache = result
    return result


def is_china_workday(d):
    """判断某天是否为中国工作日（考虑国务院放假调休）。"""
    holidays = load_china_holidays()
    data = holidays.get(d.year)
    if data is None:
        return d.weekday() < 5
    if d in data["work"]:
        return True
    if d in data["off"]:
        return False
    return d.weekday() < 5

NO_WINDOW = 0x08000000
LOCK_PORT = 47932

_PS_TOAST_TEMPLATE = """$ErrorActionPreference='Stop'
[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null
[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]|Out-Null
$app='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'
$xml=New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@'
<toast><visual><binding template="ToastText02"><text id="1">{title}</text><text id="2">{body}</text>{audio}</binding></visual></toast>
'@)
$toast=New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""


# ---------------------------------------------------------------- 通知
def show_toast(title, body, silent=True):
    """发送一条 Windows 通知。

    silent=True 时静音（无声音）；silent=False 时播放系统循环报警音，
    直到用户点开通知为止。成功返回 True。
    """
    if silent:
        audio = '<audio silent="true"/>'
    else:
        audio = '<audio src="ms-winsoundevent:Notification.Looping.Alarm" loop="true"/>'
    script = (_PS_TOAST_TEMPLATE
              .replace("{title}", escape(title))
              .replace("{body}", escape(body))
              .replace("{audio}", audio))
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=True,
        )
        return True
    except Exception as exc:
        print("[toast] 通知发送失败: %r" % (exc,))
        return False


# ---------------------------------------------------------------- 铃声
_SFX_SAMPLE_RATE = 22050


def _sine_sample(i, freq, sample_rate=_SFX_SAMPLE_RATE):
    return math.sin(2 * math.pi * freq * i / sample_rate)


def _generate_preset_wav(path, name):
    """首次运行时生成几个简单预设铃声（标准 WAV，无需外部依赖）。"""
    sample_rate = _SFX_SAMPLE_RATE
    duration = 3.0
    n = int(sample_rate * duration)
    with wave.open(path, "w") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        for i in range(n):
            t = i / sample_rate
            if name == "霓虹脉冲":
                tremolo = max(0.0, math.sin(t * math.pi * 6)) ** 0.7
                freq = 880 + 220 * math.sin(t * math.pi * 1.2)
                sample = 0.55 * tremolo * _sine_sample(i, freq)
            elif name == "深海回响":
                env = 0.6 + 0.4 * math.sin(t * math.pi * 0.8)
                sample = 0.4 * env * (_sine_sample(i, 220) + 0.3 * _sine_sample(i, 440))
            elif name == "星轨":
                beat = (t * 3) % 1.0
                amp = 1.0 if beat < 0.12 else 0.0
                sample = 0.45 * amp * (_sine_sample(i, 1040) + 0.4 * _sine_sample(i, 1560))
            else:  # 无声震动
                sample = 0.0
            sample = max(-0.95, min(0.95, sample))
            fh.writeframes(struct.pack("<h", int(sample * 32767)))


def ensure_ringtones_dir():
    if not os.path.isdir(RINGTONES_DIR):
        os.makedirs(RINGTONES_DIR, exist_ok=True)
    for name in PRESET_RINGTONES:
        path = os.path.join(RINGTONES_DIR, name + ".wav")
        if not os.path.exists(path):
            try:
                _generate_preset_wav(path, name)
            except Exception as exc:
                print("[ringtone] 生成预设铃声失败 %s: %r" % (name, exc))


def get_ringtones():
    """返回所有可用铃声（含预设和自定义上传）。"""
    ensure_ringtones_dir()
    out = []
    for name in sorted(os.listdir(RINGTONES_DIR)):
        path = os.path.join(RINGTONES_DIR, name)
        if os.path.isfile(path):
            out.append({
                "name": name,
                "custom": name not in PRESET_FILENAMES,
            })
    return out


def _strip_data_url(data):
    if isinstance(data, str) and "," in data:
        return data.split(",", 1)[1]
    return data


def upload_ringtone(filename, data_b64):
    """上传音频文件，返回铃声列表。支持 data URL 或纯 base64。"""
    ensure_ringtones_dir()
    raw = base64.b64decode(_strip_data_url(data_b64))
    base = os.path.basename(filename)
    stem, ext = os.path.splitext(base)
    safe = "".join(c for c in stem if c.isalnum() or c in "._-").strip()[:40] or "custom"
    ext = ext.lower()
    if ext not in (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac"):
        ext = ".mp3"
    target = safe + ext
    if target in PRESET_FILENAMES:
        target = "custom_" + target
    path = os.path.join(RINGTONES_DIR, target)
    counter = 1
    while os.path.exists(path):
        s, e = os.path.splitext(target)
        path = os.path.join(RINGTONES_DIR, "%s_%d%s" % (s, counter, e))
        counter += 1
    with open(path, "wb") as fh:
        fh.write(raw)
    return {"list": get_ringtones(), "saved": os.path.basename(path)}


def delete_ringtone(name):
    """删除自定义铃声（预设不可删）。"""
    ensure_ringtones_dir()
    base = os.path.basename(name)
    if base in PRESET_FILENAMES:
        return get_ringtones()
    path = os.path.join(RINGTONES_DIR, base)
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception as exc:
            print("[ringtone] 删除失败 %s: %r" % (base, exc))
    return get_ringtones()


def _play_media_ps(path, volume):
    """使用 PowerShell + WPF MediaPlayer 异步播放音频（支持 wav/mp3 等）。"""
    if not os.path.exists(path):
        return False
    vol = max(0, min(100, int(volume or 70))) / 100.0
    escaped = path.replace("'", "''")
    script = """
Add-Type -AssemblyName presentationCore
$player = New-Object System.Windows.Media.MediaPlayer
$player.Open([uri]::new('file:///%s'))
$player.Volume = %f
$done = $false
Register-ObjectEvent -InputObject $player -EventName MediaEnded -Action { $script:done = $true } | Out-Null
$player.Play()
$timeout = (Get-Date).AddSeconds(30)
while ((-not $done) -and ((Get-Date) -lt $timeout)) { Start-Sleep -Milliseconds 200 }
$player.Close()
""" % (escaped, vol)
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as exc:
        print("[audio] 播放失败: %r" % (exc,))
        return False


def play_alarm_sound(ringtone, volume=70, muted=False):
    """触发闹钟时播放选中的铃声；静音时直接返回。"""
    if muted:
        return False
    ensure_ringtones_dir()
    if not ringtone:
        ringtone = PRESET_RINGTONES[0] + ".wav"
    path = os.path.join(RINGTONES_DIR, os.path.basename(ringtone))
    if not os.path.exists(path):
        # 兼容只存显示名的情况
        path = os.path.join(RINGTONES_DIR, os.path.basename(ringtone) + ".wav")
    if os.path.exists(path):
        return _play_media_ps(path, volume)
    return False


# ---------------------------------------------------------------- 时间计算
def next_fire(alarm, now=None):
    """返回该闹钟下一次触发的 datetime（严格大于 now）。

    - 仅一次（once）：今天该时刻若未过则取今天，否则顺延到明天。
    - 中国工作日（china_workday=True）：按国务院放假调休查找下一个工作日。
    - 周期（days）：向后查找 60 天内首个落在允许星期内的时刻。
    """
    now = now or datetime.now()
    try:
        hour, minute = (int(x) for x in alarm["time"].split(":"))
    except Exception:
        return None
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if alarm.get("once"):
        for offset in range(8):
            moment = candidate + timedelta(days=offset)
            if moment > now:
                return moment
        return None
    if alarm.get("china_workday"):
        # 从候选日开始向后查找真实工作日（含调休上班）
        for offset in range(60):
            moment = candidate + timedelta(days=offset)
            if moment <= now:
                continue
            if is_china_workday(moment.date()):
                return moment
        return None
    days = alarm.get("days") or []
    for offset in range(60):
        moment = candidate + timedelta(days=offset)
        if moment > now and moment.weekday() in days:
            return moment
    return None


def active_today(alarm, now=None):
    """该闹钟今天是否会响（已启用且下次触发是今天）。"""
    now = now or datetime.now()
    if not alarm.get("enabled", True):
        return False
    nf = next_fire(alarm, now)
    return nf is not None and nf.date() == now.date()


def fmt_remaining(delta):
    seconds = max(0, int(delta.total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%d 小时 %d 分 %d 秒" % (hours, minutes, secs)
    if minutes:
        return "%d 分 %d 秒" % (minutes, secs)
    return "%d 秒" % secs


def fmt_next(moment, now):
    if moment is None:
        return "-"
    days = (moment.date() - now.date()).days
    if days == 0:
        prefix = "今天"
    elif days == 1:
        prefix = "明天"
    else:
        prefix = "周" + WEEKDAY_CN[moment.weekday()]
    return prefix + " " + moment.strftime("%H:%M")


def repeat_label(alarm):
    """把 days / once / china_workday 模型转成友好的中文重复标签。"""
    if alarm.get("once"):
        return "仅一次"
    if alarm.get("china_workday"):
        return "工作日（调休）"
    days = sorted(alarm.get("days") or [])
    if not days:
        return "未设置"
    if days == [0, 1, 2, 3, 4, 5, 6]:
        return "每天"
    if days == [0, 1, 2, 3, 4]:
        return "工作日"
    if days == [5, 6]:
        return "周末"
    return "周" + " ".join(WEEKDAY_CN[d] for d in days)


# ---------------------------------------------------------------- 迁移 / 存取
def _find_legacy(name, max_levels=3):
    """向上查找旧版数据文件（最多 3 级父目录），返回首个命中路径。"""
    cur = DATA_DIR
    for _ in range(max_levels):
        cand = os.path.join(cur, name)
        if os.path.exists(cand) and os.path.abspath(cand) != os.path.abspath(os.path.join(DATA_DIR, name)):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def _migrate_legacy():
    for name in ("alarms.json", "settings.json"):
        dst = os.path.join(DATA_DIR, name)
        if os.path.exists(dst):
            continue
        src = _find_legacy(name)
        if src:
            try:
                shutil.copyfile(src, dst)
            except Exception:
                pass


def _normalize(item):
    """把一条（可能来自旧版的）闹钟记录规整为 days 模型。失败返回 None。"""
    try:
        time_str = item["time"]
        datetime.strptime(time_str, "%H:%M")
    except Exception:
        return None
    alarm_id = item.get("id")
    if not isinstance(alarm_id, int):
        return None
    once = bool(item.get("once")) or item.get("repeat") == "once"
    china_workday = bool(item.get("china_workday"))
    if "days" in item and isinstance(item["days"], list):
        days = [int(d) for d in item["days"] if 0 <= int(d) <= 6]
    elif item.get("repeat") in _LEGACY_DAYS:
        days = list(_LEGACY_DAYS[item["repeat"]])
    else:
        days = list(_LEGACY_DAYS["daily"])
    return {
        "id": alarm_id,
        "time": time_str,
        "label": str(item.get("label") or "闹钟"),
        "enabled": bool(item.get("enabled", True)),
        "days": days,
        "once": once,
        "china_workday": china_workday,
        "ringtone": str(item.get("ringtone") or "霓虹脉冲"),
        "snooze": bool(item.get("snooze", False)),
        "volume": int(item.get("volume", 70)),
    }


def load_alarms():
    _migrate_legacy()
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return []
    alarms = []
    seen = set()
    for item in raw if isinstance(raw, list) else []:
        a = _normalize(item)
        if a is None or a["id"] in seen:
            continue
        seen.add(a["id"])
        alarms.append(a)
    return alarms


def save_alarms(alarms):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(alarms, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def next_id(alarms):
    return max((a["id"] for a in alarms), default=0) + 1


# ---------------------------------------------------------------- 设置
def load_settings():
    """读取全局设置，缺省：静音（muted=True）。"""
    defaults = {"muted": True}
    if not os.path.exists(SETTINGS_FILE):
        return defaults
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {"muted": bool(data.get("muted", True))}
    except Exception:
        pass
    return defaults


def save_settings(settings):
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)


# ---------------------------------------------------------------- 单实例
def acquire_single_instance():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR：避免上次退出后端口仍处于 TIME_WAIT 时误判为「已在运行」
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except OSError:
        pass
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


# ---------------------------------------------------------------- 自检
def selftest():
    """不弹窗的逻辑自检 + 通知通道实测。"""
    global DATA_FILE, SETTINGS_FILE
    import tempfile
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok = ok and cond

    with tempfile.TemporaryDirectory(prefix="chronos_selftest_") as tmp:
        DATA_FILE = os.path.join(tmp, "alarms.json")
        SETTINGS_FILE = os.path.join(tmp, "settings.json")
        save_alarms([{
            "id": 1, "time": "07:30", "label": "早起", "enabled": True,
            "days": [0, 1, 2, 3, 4, 5, 6], "once": False,
        }])
        data = load_alarms()
        check("save/load roundtrip", len(data) == 1 and data[0]["time"] == "07:30")

        now = datetime(2026, 10, 14, 9, 0, 0)  # 周三上午 9 点
        check("once past -> tomorrow",
              next_fire({"time": "07:30", "once": True}, now)
              == datetime(2026, 10, 15, 7, 30))
        check("once future -> today",
              next_fire({"time": "10:00", "once": True}, now)
              == datetime(2026, 10, 14, 10, 0))
        check("weekdays -> tomorrow",
              next_fire({"time": "07:30", "days": [0, 1, 2, 3, 4]}, now)
              == datetime(2026, 10, 15, 7, 30))
        sat = datetime(2026, 10, 17, 12, 0)  # 周六中午
        check("weekdays on Sat -> next Mon",
              next_fire({"time": "09:00", "days": [0, 1, 2, 3, 4]}, sat)
              == datetime(2026, 10, 19, 9, 0))
        check("daily today 23:00",
              next_fire({"time": "23:00", "days": list(range(7))}, now)
              == datetime(2026, 10, 14, 23, 0))
        check("fmt_remaining",
              fmt_remaining(timedelta(hours=2, minutes=3, seconds=4)) == "2 小时 3 分 4 秒")
        check("repeat_label daily",
              repeat_label({"days": list(range(7))}) == "每天")
        check("repeat_label weekend",
              repeat_label({"days": [5, 6]}) == "周末")
        sun = datetime(2026, 10, 18, 8, 0)  # 周日
        check("active_today weekdays on Sun -> False",
              active_today({"time": "07:30", "enabled": True, "days": [0, 1, 2, 3, 4]}, sun) is False)

        # 中国调休工作日测试（2026 年国庆节 10.1-10.7 放假，10.10 周六调休上班）
        oct14_morning = datetime(2026, 10, 14, 9, 0, 0)  # 周三，正常上班
        check("china_workday next normal",
              next_fire({"time": "08:00", "china_workday": True}, oct14_morning)
              == datetime(2026, 10, 15, 8, 0))
        oct8_holiday = datetime(2026, 10, 8, 9, 0, 0)  # 国庆假期内
        check("china_workday skip holiday",
              next_fire({"time": "08:00", "china_workday": True}, oct8_holiday)
              == datetime(2026, 10, 9, 8, 0))
        oct9_fri = datetime(2026, 10, 9, 9, 0, 0)  # 周五，时间已过 8:00
        check("china_workday use adjusted Sat workday",
              next_fire({"time": "08:00", "china_workday": True}, oct9_fri)
              == datetime(2026, 10, 10, 8, 0))
        check("repeat_label china_workday",
              repeat_label({"china_workday": True}) == "工作日（调休）")
        check("is_china_workday 2026-10-10 Sat work",
              is_china_workday(datetime(2026, 10, 10).date()) is True)
        check("is_china_workday 2026-10-07 Wed holiday",
              is_china_workday(datetime(2026, 10, 7).date()) is False)

    check("legacy migration map",
          _LEGACY_DAYS["weekdays"] == [0, 1, 2, 3, 4])
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest())
