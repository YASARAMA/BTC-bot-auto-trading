# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds BTCBot (windowed) and BTCBot-console (with a console, for troubleshooting).
# Build:  pyinstaller --clean --noconfirm btcbot.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = collect_submodules("ccxt") + collect_submodules("pydantic") + ["webview", "clr", "clr_loader", "pythonnet"]
datas = [
    ("config.yaml", "."),
    ("data/samples", "data/samples"),
    ("bot/ui/static", "bot/ui/static"),
]
datas += collect_data_files("certifi")
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

exe_gui = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="BTCBot",
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
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
)
