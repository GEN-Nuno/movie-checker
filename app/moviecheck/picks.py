"""『今夜のあなたに』の選び方 (MV-9)。景色 (3 枚の札)・行動 (手)・余韻 (今週の 1 行)。

choose: WL で上映中の物 → taste の点数順 → 同じ監督は 1 枚 → 学んでいれば 1 枚は 🎲 (未知)。当日『いまいち』にした物は出さない。
mood_filter: あらすじの語で絞る (学習には使わない)。0 件なら全部に戻す。
afterglow: 7 日分の反応を 監督 / 出演 で集計して 1 行。
"""
from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from .match import find_match
from .store import load_json, save_json
from . import taste

MOODS = {
    "おまかせ": [],
    "笑いたい": ["コメディ", "笑", "爆笑", "ユーモア", "ドタバタ", "痛快"],
    "泣きたい": ["感動", "涙", "愛", "絆", "別れ", "喪失", "家族"],
    "迫力": ["アクション", "戦", "バトル", "冒険", "サスペンス", "スリル", "スペクタクル", "ホラー"],
    "静かに": ["日常", "静か", "ドラマ", "人生", "ドキュメンタリー", "青春", "記憶", "詩"],
}
N_PICKS = 3


def mood_filter(movies: list[dict], mood: str) -> tuple[list[dict], str]:
    """(残った物, 注)。語が無い気分 (おまかせ) は全部。0 件なら全部と注。"""
    words = MOODS.get(mood) or []
    if not words:
        return list(movies), ""
    kept = [m for m in movies if any(w in (m.get("synopsis") or "") + (m.get("title") or "") for w in words)]
    if not kept:
        return list(movies), f"『{mood}』の物は見つからず、おまかせで"
    return kept, ""


def wl_showing_titles(watchlist: list, dir_watchlist: list, now_showing: dict) -> dict[str, str]:
    """今上映中の WL の題名 → 訳 (観たい映画 / 監督 ○○ の作品)。"""
    out: dict[str, str] = {}
    for w in watchlist or []:
        mt = find_match(w.get("title", ""), now_showing)
        if mt:
            out.setdefault(mt, "観たい映画で上映中")
    ids = {m.get("movie_id"): t for t, m in now_showing.items() if m.get("movie_id")}
    for d in dir_watchlist or []:
        for m in d.get("movies", []):
            t = ids.get(m.get("movie_id")) or (m.get("title") if m.get("title") in now_showing else None)
            if t:
                out.setdefault(t, f"監督 {d.get('name', '')} の作品")
    return out


def choose(movies: list[dict], wl_reasons: dict[str, str], model: taste.Model, dismissed: set[str],
           rng: random.Random | None = None, n: int = N_PICKS) -> list[dict]:
    """movies は上映中の順 (人気順)。戻り [{movie, reason, kind}] (kind: wl | taste | explore | rank)。"""
    rng = rng or random.Random()
    cands = [m for m in movies if m.get("movie_id") not in dismissed]
    scores = {s["title"]: s for s in model.score_all(cands, rng=rng, explore=False)}
    picked: list[dict] = []
    used_dirs: set[str] = set()
    used_titles: set[str] = set()

    def take(m, reason, kind):
        d = (m.get("director") or "").strip()
        if m.get("title") in used_titles or (d and d in used_dirs):
            return False
        picked.append({"movie": m, "reason": reason, "kind": kind})
        used_titles.add(m.get("title"))
        if d:
            used_dirs.add(d)
        return True

    for m in cands:                       # (1) WL で上映中
        if len(picked) >= n:
            break
        if m.get("title") in wl_reasons:
            take(m, wl_reasons[m["title"]], "wl")
    if model.learned():                   # (2) 点数順
        for m in sorted(cands, key=lambda x: -scores.get(x.get("title"), {}).get("score", 0.0)):
            if len(picked) >= max(n - 1, 1):
                break
            s = scores.get(m.get("title"), {})
            if s.get("score", 0.0) >= taste.STAR1:
                take(m, "なぜ: " + s.get("why", ""), "taste")
        if len(picked) < n:               # (3) 1 枚は 🎲
            unknown = [m for m in cands if scores.get(m.get("title"), {}).get("unknown") and m.get("title") not in used_titles]
            rng.shuffle(unknown)
            for m in unknown:
                if take(m, "🎲 試しに (まだ分からない物)", "explore"):
                    break
    for i, m in enumerate(cands, 1):      # (4) 残りは人気順
        if len(picked) >= n:
            break
        take(m, f"人気 {i} 位", "rank")
    return picked[:n]


# ---- 当日の『いまいち』 ----
def load_dismissed(data_dir: Path, today: str) -> set[str]:
    st = load_json(Path(data_dir) / "picks_state.json")
    if isinstance(st, dict) and st.get("date") == today:
        return set(str(x) for x in st.get("dismissed") or [])
    return set()


def add_dismissed(data_dir: Path, today: str, movie_id: str) -> set[str]:
    s = load_dismissed(data_dir, today)
    if movie_id:
        s.add(str(movie_id))
    save_json(Path(data_dir) / "picks_state.json", {"date": today, "dismissed": sorted(s)})
    return s


# ---- 余韻 ----
def afterglow(feedback: list[dict], now_showing: dict, now: datetime | None = None, days: int = 7) -> str:
    """7 日分の反応から『今週: 監督 ○○ に惹かれています (観たい 2・👍 1)』。無ければ空。"""
    now = now or datetime.now()
    since = now - timedelta(days=days)
    recent = []
    for f in feedback or []:
        try:
            at = datetime.strptime(f.get("at", ""), "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if at >= since and f.get("r", 0) > 0:
            recent.append(f)
    if not recent:
        return ""
    who: Counter = Counter()
    for f in recent:
        m = now_showing.get(f.get("title")) or {}
        d = (m.get("director") or "").strip()
        if d:
            who["監督 " + d] += 1
        for c in (m.get("cast") or [])[:3]:
            who["出演 " + c] += 1
    kinds = Counter(f.get("kind") for f in recent)
    n_wl, n_good = kinds.get("wl", 0), kinds.get("good", 0)
    detail = "・".join(x for x in [f"観たい {n_wl}" if n_wl else "", f"👍 {n_good}" if n_good else ""] if x)
    if who:
        name, cnt = who.most_common(1)[0]
        if cnt >= 2:
            return f"今週: {name} に惹かれています ({detail})"
    return f"今週: {len(recent)} 本に反応 ({detail})"
