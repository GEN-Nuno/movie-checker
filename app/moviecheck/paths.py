"""データの置き場 (MV-4)。

- 源から動かす: .py の隣 (今までどおり)。
- exe: 隣に watchlist.json か settings.json があれば隣 (OneDrive で持ち運ぶ人)。
  無ければ %APPDATA%\\映画チェッカー (インストール版。無ければ作る)。
- 初回 (置き場が空) に、以前の exe の隣の json を写せる (import_from)。元は消さない。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

APP_NAME = "映画チェッカー"
DATA_FILES = ("watchlist.json", "director_watchlist.json", "settings.json",
              "movie_cache.json", "notify_state.json", "notify_log.json")
_MARKERS = ("watchlist.json", "settings.json")


def data_dir(app_dir: Path, frozen: bool, appdata: str | None = None) -> Path:
    """置き場を決める。appdata は試験で差し替える (既定は %APPDATA%)。"""
    app_dir = Path(app_dir)
    if not frozen:
        return app_dir
    if any((app_dir / m).exists() for m in _MARKERS):
        return app_dir
    base = appdata if appdata is not None else os.environ.get("APPDATA", "")
    if not base:
        return app_dir
    d = Path(base) / APP_NAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return app_dir
    return d


def is_empty(d: Path) -> bool:
    """まだ何も置いていない (watchlist / settings が無い)。"""
    return not any((Path(d) / m).exists() for m in _MARKERS)


def import_from(src: Path, dst: Path) -> tuple[list[str], list[str]]:
    """src にある DATA_FILES を dst に写す。戻り (写した名前, 訳)。壊れていた物は写さず訳に残す。元は触らない。"""
    src, dst = Path(src), Path(dst)
    copied: list[str] = []
    notes: list[str] = []
    if src.resolve() == dst.resolve():
        return copied, ["同じフォルダです"]
    for name in DATA_FILES:
        s = src / name
        if not s.is_file():
            continue
        t = dst / name
        try:
            shutil.copy2(s, t)
            with open(t, encoding="utf-8") as f:
                json.load(f)
            copied.append(name)
        except (OSError, ValueError) as e:
            try:
                t.unlink(missing_ok=True)
            except OSError:
                pass
            notes.append(f"{name} は写せませんでした ({type(e).__name__})")
    return copied, notes


def build_info(bundle_dir: Path) -> dict:
    """固めた時の情報 (build_info.json、build.ps1 が exe に同梱)。源から動かす時は空。"""
    p = Path(bundle_dir) / "build_info.json"
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}
