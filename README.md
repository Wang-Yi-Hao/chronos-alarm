# Chronos 闹钟（深蓝科技风桌面版）

![Chronos 闹钟图标](icon/appicon.png)

基于资料库「闹钟」文件夹中的 **Chronos 深蓝科技风设计系统** 改造而来的桌面闹钟软件。

- GitHub 仓库：[https://github.com/Wang-Yi-Hao/chronos-alarm](https://github.com/Wang-Yi-Hao/chronos-alarm)
- 直接下载（Windows）：[Releases](https://github.com/Wang-Yi-Hao/chronos-alarm/releases) → 下载最新的 `Chronos-vX.Y.Z.zip`，解压后双击 `Chronos/Chronos.exe` 即可运行

用 `pywebview` 原生窗口承载这套 HTML/CSS UI，后端复用原 Windows 闹钟的核心逻辑
（Windows Toast 通知、`alarms.json` 持久化、单实例锁、贪睡）。

四大模块完整还原设计系统：**时钟主页 / 闹钟（列表·新建·编辑·删除·开关）/ 计时器 / 秒表**。

新增特性：

- **NTP 时间校准**：每次启动（含开机自启）自动向 `ntp.aliyun.com / ntp.tencent.com / cn.pool.ntp.org / time.windows.com` 依次查询标准时间，主窗口时钟与悬浮窗时间随即切到服务器时间，所有闹钟判定也用校准后的时间（本机时间错几分钟也不会响错点）。若与本机系统时间相差 ≥5 秒，会提示提权把系统时间一并改正（只需授权一次）；也可点顶栏「已校时」胶囊随时手动提权同步。运行中每 2 小时复查一次。
- **桌面悬浮窗**：340×376 常驻桌面右下角，显示当前时间（秒位青色）、日期与「已开启的闹钟」（按下次响铃升序：剩余时间 / 今天 / 明天）。按住时钟/日期/标题行等空白处即可拖动，松手后靠近屏幕边缘（24px 内）自动吸附贴边；折叠/展开会根据所在位置自动选择锚点——贴近底部时底边固定、向上展开（折叠时向下收起），贴近顶部时顶边固定、向下展开。位置自动记忆；双击或点箭头折叠为 340×62 胶囊条（同样带青色秒数，整条都可拖动）；图钉切换置顶；点击某条闹钟跳回主窗口并高亮定位；临近 30 分钟首条转为青色呼吸光晕；响铃中整卡琥珀警示并把时间位替换为「停止 / 贪睡 5 分钟」；没有启用的闹钟时降级为空态（一键打开主窗口）。主窗口顶栏的「悬浮窗」开关可随时隐藏/恢复，悬浮窗不占任务栏按钮也不进 Alt+Tab。
- **时/分下拉选择**：新建/编辑闹钟的时间改为「小时 / 分钟」两个下拉选择器，不用手输也不用点系统时间弹层，误操作更少。
- **仅一次闹钟**：重复规则新增「仅一次」，到点响一次后自动关闭（同时移除对应的系统计划任务），不再按天循环；错过当天会自动停用，不会第二天补响。
- **音量 0–100%（每 10% 一档）**：音量改为 11 档下拉，0% 即静音，按闹钟单独保存。
- **中国调休工作日**：新建/编辑闹钟时选择「工作日」，会自动按国务院公布的放假调休安排判断真实工作日（默认内置 2025、2026 年数据），遇节假日顺延、遇补班提前响。也可在数据目录放 `holidays.json` 自行扩展/更新。
- **自定义铃声**：闹钟表单支持上传本地音频文件（wav / mp3 / m4a / ogg 等），预设铃声会在首次运行时生成简单 WAV，提醒时通过 PowerShell MediaPlayer 异步播放。
- **系统托盘常驻**：点击窗口「关闭」按钮不会退出程序，而是**最小化到系统托盘**（右下角图标），闹钟继续在后台正常提醒；双击托盘图标或右键「显示窗口」可重新打开。右键托盘菜单「退出」才真正关闭。
- **开机自启**：顶栏新增「开机自启」开关。开启后会在「当前用户登录时自动后台启动（写入 `HKCU\...\Run`，带 `--hidden` 静默缩到托盘）」；关闭即移除。重启电脑后闹钟依旧会在后台准时响铃。
- **关闭软件也能响（系统任务计划）**：每个**启用**的闹钟会在 Windows 任务计划程序里自动注册一个「每天 HH:MM」的任务，动作是启动本程序的无窗口静默模式（`Chronos.exe --alarm <id>`）。因此即使你关掉了软件、或刚重启电脑（配合开机自启），到点也由系统唤醒闹钟响铃通知+铃声。系统任务与运行中的检查线程通过单实例锁去重，不会重复响；增删/编辑/开关闹钟时自动同步对应任务。

## 运行（开发模式）

```bash
pip install -r requirements.txt      # 安装 pywebview
python main.py                       # 启动（也可双击 run.bat）
```

首次运行会自动从上级目录（旧版 `alarms.json` / `settings.json`）迁移已有闹钟数据。

## 打包为 .exe（一键）

最简单：**双击 `build.bat`** 即可自动创建虚拟环境、安装依赖并完成打包。

或手动：

```bash
pip install pyinstaller
python -m PyInstaller build.spec --noconfirm --clean
```

产物为 `dist/Chronos/` 整个目录（`Chronos.exe` + 同目录的 `_internal/` 依赖）。
**双击 `dist/Chronos/Chronos.exe` 即可启动**，但必须连同 `_internal/` 一起分发（onedir 形态）。
`build.bat` 已指向专用虚拟环境 `...\envs\chronos`，首次运行会自动 `pip install pywebview pyinstaller`。

> 说明：
> - pywebview 在 Windows 上复用系统自带的 **WebView2**（Win10/11 内置），无需额外下载浏览器内核。
> - 若目标机器没有 WebView2 运行时，到微软官网安装「WebView2 Runtime（Evergreen）」即可。
> - 闹钟数据（`alarms.json` / `settings.json` / `ringtones/`）默认写在 `Chronos.exe` 同级目录，随 exe 便携移动。

## 命令行

| 参数 | 作用 |
| --- | --- |
| `--selftest` | 运行核心逻辑自检（时间计算、存取、迁移等），不弹窗 |
| `--test-notify` | 发送一条测试 Windows 通知 |

## 目录结构

```
chronos/
├── main.py            # pywebview 外壳 + ChronosAPI 后端桥接 + 到点检查线程
├── core.py            # 闹钟核心逻辑（无 GUI 依赖，可独立测试）
├── requirements.txt
├── build.spec         # PyInstaller onedir 配置
├── run.bat / build.bat
├── icon/              # 闹钟图标（appicon.ico / appicon.png / make_icon.py）
└── ui/
    ├── index.html     # 五视图结构：时钟/闹钟/新建表单/计时/秒表
    ├── style.css      # Chronos 深蓝科技风设计系统
    └── app.js         # 前端逻辑 + window.pywebview.api 桥接
```

## 数据

- 闹钟数据：`chronos/alarms.json`（首次运行从旧版迁移）
- 设置（声音提醒开关）：`chronos/settings.json`
- 节假日覆盖：`chronos/holidays.json`（可选，格式见下方）
- 铃声文件：`chronos/ringtones/`（预设 + 自定义上传）
- 单实例锁：`127.0.0.1:47932`

### holidays.json 格式示例

```json
{
  "2027": {
    "off": ["2027-01-01", "2027-02-11", "2027-02-12"],
    "work": ["2027-02-06", "2027-02-20"]
  }
}
```

## 已知限制

- 自定义铃声播放依赖 PowerShell + WPF MediaPlayer，因此上传的音频格式需为 Windows 原生支持的格式（wav / mp3 / m4a / ogg 等）。
- 铃声最长播放 30 秒，或播完自动停止。
- 设计系统的时间/日期字面量按中文环境展示。
