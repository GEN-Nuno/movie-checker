# -*- mode: python ; coding: utf-8 -*-
# 映画チェッカー を Mac 用に固める。Windows 用は movie.spec (そのまま)。
#   dist/mac/映画チェッカー.app   (onedir を .app に包む。dmg と zip は packaging/build_mac.sh が作る)
# build/build_info.json (固めた日・Python) と build/movie_icon.icns は build_mac.sh が先に書く。
import re
from pathlib import Path

ROOT = Path(SPECPATH).parent
APP = ROOT / "app"
INFO = ROOT / "build" / "build_info.json"
ICNS = ROOT / "build" / "movie_icon.icns"
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (APP / "moviecheck" / "version.py").read_text(encoding="utf-8")).group(1)

datas = [(str(APP / "movie_icon.ico"), ".")]
if INFO.exists():
    datas.append((str(INFO), "."))
icon = str(ICNS) if ICNS.exists() else None

a = Analysis(
    [str(APP / "movie_app.py")],
    pathex=[str(APP)],
    binaries=[],
    datas=datas,
    hiddenimports=["keyring.backends.macOS"],   # パスワードは Mac のキーチェーンに
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "pip_audit", "PyInstaller", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="映画チェッカー",
    icon=icon,
    console=False,
    upx=False,
    strip=False,
    bootloader_ignore_signals=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="映画チェッカー")

app = BUNDLE(
    coll,
    name="映画チェッカー.app",
    icon=icon,
    bundle_identifier="jp.gen-nuno.moviechecker",
    info_plist={
        "CFBundleName": "映画チェッカー",
        "CFBundleDisplayName": "映画チェッカー",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "LSApplicationCategoryType": "public.app-category.entertainment",
    },
)
