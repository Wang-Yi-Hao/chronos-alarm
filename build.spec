# -*- mode: python ; coding: utf-8 -*-
"""Chronos 闹钟 PyInstaller 打包配置（onedir，禁用 onefile）。

运行（在已安装 pywebview + pyinstaller 的虚拟环境中）：
    python -m PyInstaller build.spec --noconfirm --clean
产物位于 dist/Chronos/Chronos.exe（onedir 目录，双击即可启动）。

说明：
- pywebview 在 Windows 使用 edgechromium 后端，依赖 WebView2 原生 DLL
  （webview/lib 下）与 pythonnet（clr / Python.Runtime.dll）。
- 这些原生文件不会被 PyInstaller 自动收集，需在下方显式加入 datas/binaries。
- 关闭 UPX，避免对 .NET 程序集（Python.Runtime.dll）造成损坏。
"""

import os
import webview
import pythonnet

APP_HOME = SPECPATH  # PyInstaller 注入：当前 spec 所在目录

WEBVIEW_DIR = os.path.dirname(webview.__file__)
PYTHONNET_DIR = os.path.dirname(pythonnet.__file__)

# WebView2 原生互操作 DLL 与 runtimes 加载器
WV_LIB = os.path.join(WEBVIEW_DIR, "lib")
# pythonnet 的 .NET 运行时程序集
PN_RUNTIME_DLL = os.path.join(PYTHONNET_DIR, "runtime", "Python.Runtime.dll")
PN_RUNTIME_JSON = os.path.join(PYTHONNET_DIR, "runtime", "Python.Runtime.deps.json")

a = Analysis(
    ["main.py"],
    pathex=[APP_HOME],
    binaries=[
        (PN_RUNTIME_DLL, "pythonnet/runtime"),
        (PN_RUNTIME_JSON, "pythonnet/runtime"),
    ],
    datas=[
        (os.path.join(APP_HOME, "ui"), "ui"),
        (os.path.join(APP_HOME, "icon"), "icon"),
        (WV_LIB, "webview/lib"),
    ],
    hiddenimports=[
        "core",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "webview.platforms.win32",
        "clr",
        "clr_loader",
        "pythonnet",
    ],
    excludes=[
        "cefpython3",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "gi", "wx",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Chronos",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # 关闭 UPX：保护 .NET 程序集
    console=False,           # 桌面 GUI，无控制台窗口；异常写入 error.log
    icon=os.path.join(APP_HOME, "icon", "appicon.ico"),
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    name="Chronos",
)
