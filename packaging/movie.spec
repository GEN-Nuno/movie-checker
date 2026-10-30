# -*- mode: python ; coding: utf-8 -*-
# 映画チェッカー を固める (MV-3 / MV-4)。1 回の解析から 2 つ出す:
#   dist/映画チェッカー/           onedir (setup.exe に入れる。起動が速い)
#   dist/映画チェッカー.exe        onefile (OneDrive で持ち運ぶ人の為。今までどおり)
# 使い方は packaging/build.ps1。build/build_info.json (固めた日・Python) は build.ps1 が先に書く。
from pathlib import Path

ROOT = Path(SPECPATH).parent
APP = ROOT / "app"
INFO = ROOT / "build" / "build_info.json"

datas = [(str(APP / "movie_icon.ico"), ".")]
if INFO.exists():
    datas.append((str(INFO), "."))

a = Analysis(
    [str(APP / "movie_app.py")],
    pathex=[str(APP)],
    binaries=[],
    datas=datas,
    hiddenimports=["keyring.backends.Windows"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "pip_audit", "PyInstaller", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)

COMMON = dict(
    name="映画チェッカー",
    icon=str(APP / "movie_icon.ico"),
    console=False,           # 画面のアプリ。--send-mail の出力は見えない (記録は notify_log.json)
    upx=False,
    bootloader_ignore_signals=False,
    strip=False,
    disable_windowed_traceback=False,
)

# onedir
exe_dir = EXE(pyz, a.scripts, [], exclude_binaries=True, **COMMON)
coll = COLLECT(exe_dir, a.binaries, a.datas, strip=False, upx=False, name="映画チェッカー")

# onefile
exe_one = EXE(pyz, a.scripts, a.binaries, a.datas, [], **COMMON)
