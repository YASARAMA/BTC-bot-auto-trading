# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds BTCBot (windowed) and BTCBot-console (with a console, for troubleshooting).
# Build:  pyinstaller --clean --noconfirm btcbot.spec
from PyInstaller.building.splash import Splash
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = collect_submodules("ccxt") + collect_submodules("pydantic") + ["webview", "clr", "clr_loader", "pythonnet"]
datas = [
    ("config.yaml", "."),
    ("CHANGELOG.md", "."),
    ("data/samples", "data/samples"),
    ("bot/ui/static", "bot/ui/static"),
    ("bot/license_hashes.json", "bot"),
    ("assets/icon.png", "assets"),
]
datas += collect_data_files("certifi")
import os as _os
if _os.path.exists("bot/ui/build_info.json"):
    datas.append(("bot/ui/build_info.json", "bot/ui"))
try:
    datas += collect_data_files("webview")
except Exception:  # pywebview not installed on this platform
    pass

a = Analysis(
    ["run_gui.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "IPython", "notebook", "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# Shown while Windows unpacks the one-file executable, which is the slow part of a cold
# start. PyInstaller's splash needs tkinter; on a machine without it we build without one
# rather than failing the whole build.
try:
    import tkinter  # noqa: F401  (PyInstaller's splash is built with tkinter)

    splash = Splash(
        "assets/splash.png",
        binaries=a.binaries,
        datas=a.datas,
        text_pos=(60, 258),
        text_size=11,
        text_color="#8b98a8",
        minify_script=True,
        always_on_top=False,
    )
    splash_parts = [splash, splash.binaries]
except (ImportError, SystemExit, Exception) as exc:  # noqa: BLE001 - no tkinter on this machine
    print(f"btcbot.spec: building without a splash screen ({type(exc).__name__}: {exc})")
    splash_parts = []

exe_gui = EXE(
    pyz, a.scripts, *splash_parts, a.binaries, a.datas, [],
    name="BTCBot",
    icon="assets/icon.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
)
exe_console = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="BTCBot-console",
    icon="assets/icon.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
)
