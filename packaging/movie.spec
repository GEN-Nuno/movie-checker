# -*- mode: python ; coding: utf-8 -*-
# 映画チェッカー を 1 つの exe に固める (MV-3)。使い方は packaging/build.ps1
from pathlib import Path

ROOT = Path(SPECPATH).parent
APP = ROOT / "app"

a = Analysis(
    [str(APP / "movie_app.py")],
    pathex=[str(APP)],
    binaries=[],
    datas=[(str(APP / "movie_icon.ico"), ".")],
    hiddenimports=["keyring.backends.Windows"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "pip_audit", "PyInstaller", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    [],
    name="映画チェッカー",
    icon=str(APP / "movie_icon.ico"),
    console=False,           # 画面のアプリ。--send-mail の出力は見えない (記録は notify_log.json)
    upx=False,
    bootloader_ignore_signals=False,
    strip=False,
    disable_windowed_traceback=False,
)
