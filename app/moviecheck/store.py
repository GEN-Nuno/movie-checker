"""json の読み書き (MV-1): tmp に書いて rename、前の物を .bak に 1 世代。壊れていたら .bak から戻す。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# 読み込みで起きた事 (画面が状態欄に出す)。{path.name: 訳}
last_notes: dict[str, str] = {}


def _empty_for(path: Path):
    return [] if "watchlist" in path.name else {}


def _read(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_json(path: Path):
    """読む。無ければ空 (watchlist は配列、他は辞書)。壊れていれば .bak、それも駄目なら空にして訳を残す。"""
    last_notes.pop(path.name, None)
    if not path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        if bak.exists():
            try:
                data = _read(bak)
                last_notes[path.name] = f"{path.name} が無いので退避 (.bak) から戻しました"
                return data
            except (OSError, ValueError):
                pass
        return _empty_for(path)
    try:
        return _read(path)
    except (OSError, ValueError) as e:
        bak = path.with_suffix(path.suffix + ".bak")
        if bak.exists():
            try:
                data = _read(bak)
                last_notes[path.name] = f"{path.name} が壊れていたので退避 (.bak) から戻しました"
                return data
            except (OSError, ValueError):
                pass
        last_notes[path.name] = f"{path.name} が壊れていて退避も無いので空にしました ({type(e).__name__})"
        return _empty_for(path)


def save_json(path: Path, data: Any) -> None:
    """tmp に書く → 前の物を .bak に → tmp を本名に。どこで落ちても本名か .bak のどちらかは残る。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    bak = path.with_suffix(path.suffix + ".bak")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if path.exists():
        os.replace(path, bak)
    os.replace(tmp, path)
