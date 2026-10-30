"""好みの学習 (MV-8)。手元だけの線形バンディット。外の AI には送らない。

特徴: 監督 (dir:名)、題名の語 (tok:語、NFKC の後の 2 字以上の塊)、順位の帯 (rank:top10 / top30 / rest)。
報酬: WL 追加 +1、監督 WL の作品 +0.5、興味なし -1、観た 👍 +2、観た 👎 -1。
更新: p = Σ w[f]; w[f] += η_f (r − p)、η_f = 0.1 / √counts[f] (多く見た特徴ほど動かない)、w は ±3 に収める。
探索: counts の合計が 2 未満 (未知) の物を ε=0.1 の確率で 1 件『試しに』。
評価: WL の 1 つ抜き (leave_one_out) で、抜いた物が上映中なら順位。無作為なら N/2。
"""
from __future__ import annotations

import math
import random
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from .store import load_json, save_json

ETA = 0.1
CLIP = 3.0
EPSILON = 0.1
UNKNOWN_MAX = 2          # counts の合計がこれ未満なら『未知』
STAR1, STAR2 = 0.5, 1.5  # ★ / ★★ の点数
FEEDBACK_MAX = 500
REWARDS = {"wl": 1.0, "dirwl": 0.5, "skip": -1.0, "good": 2.0, "bad": -1.0}
LABELS = {"wl": "WL に追加", "dirwl": "監督の作品", "skip": "興味なし", "good": "観た 👍", "bad": "観た 👎"}

_TOKEN_RE = re.compile(r"[一-鿿]{2,}|[゠-ヿー]{2,}|[぀-ゟ]{3,}|[A-Za-z0-9]{2,}")


def features(movie: dict, rank: int | None = None) -> list[str]:
    """movie {title, director}、rank は上映中の順位 (1 始まり、無ければ None)。"""
    out: list[str] = []
    d = (movie.get("director") or "").strip()
    if d:
        out.append("dir:" + d)
    title = unicodedata.normalize("NFKC", movie.get("title") or "")
    seen = set()
    for tok in _TOKEN_RE.findall(title):
        t = tok.lower()
        if t not in seen:
            seen.add(t)
            out.append("tok:" + t)
    if rank is not None:
        out.append("rank:" + ("top10" if rank <= 10 else "top30" if rank <= 30 else "rest"))
    return out


class Model:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.w: dict[str, float] = {}
        self.counts: dict[str, int] = {}
        self.feedback: list[dict] = []
        if self.path and self.path.exists():
            d = load_json(self.path)
            if isinstance(d, dict):
                self.w = {k: float(v) for k, v in (d.get("weights") or {}).items()}
                self.counts = {k: int(v) for k, v in (d.get("counts") or {}).items()}
                self.feedback = list(d.get("feedback") or [])

    # ---- 学習 ----
    def score(self, feats: list[str]) -> tuple[float, list[tuple[str, float]]]:
        """(点数, 効いた特徴 上位 3 [(特徴, 重み)])。"""
        parts = [(f, self.w[f]) for f in feats if f in self.w]
        p = sum(v for _, v in parts)
        parts.sort(key=lambda x: -abs(x[1]))
        return p, parts[:3]

    def update(self, feats: list[str], r: float) -> float:
        p, _ = self.score(feats)
        err = r - p
        for f in feats:
            self.counts[f] = self.counts.get(f, 0) + 1
            eta = ETA / math.sqrt(self.counts[f])
            self.w[f] = max(-CLIP, min(CLIP, self.w.get(f, 0.0) + eta * err))
        return p

    def feedback_on(self, movie: dict, kind: str, rank: int | None = None, now: datetime | None = None) -> float:
        """反応を 1 つ学ぶ。kind は REWARDS の鍵。戻りは学ぶ前の点数。"""
        r = REWARDS[kind]
        p = self.update(features(movie, rank), r)
        self.feedback.append({"at": (now or datetime.now()).strftime("%Y-%m-%d %H:%M"), "title": movie.get("title", ""), "kind": kind, "r": r})
        self.feedback = self.feedback[-FEEDBACK_MAX:]
        self.save()
        return p

    def is_unknown(self, feats: list[str]) -> bool:
        return sum(self.counts.get(f, 0) for f in feats) < UNKNOWN_MAX

    def learned(self) -> bool:
        return bool(self.feedback)

    def tally(self) -> dict:
        up = sum(1 for f in self.feedback if f.get("r", 0) > 0)
        down = sum(1 for f in self.feedback if f.get("r", 0) < 0)
        return {"up": up, "down": down, "n": len(self.feedback)}

    def forget(self, feat: str) -> None:
        self.w.pop(feat, None)
        self.counts.pop(feat, None)
        self.save()

    def top(self, n: int = 10) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """(好き 上位 n, 嫌い 上位 n)。"""
        items = sorted(self.w.items(), key=lambda x: -x[1])
        likes = [(f, v) for f, v in items if v > 0][:n]
        dislikes = [(f, v) for f, v in reversed(items) if v < 0][:n]
        return likes, dislikes

    def save(self) -> None:
        if self.path:
            save_json(self.path, {"weights": self.w, "counts": self.counts, "feedback": self.feedback})

    # ---- 一覧の点数 ----
    def score_all(self, movies: list[dict], rng: random.Random | None = None, explore: bool = True) -> list[dict]:
        """上映中の一覧 (順に rank) に点数・星・なぜ を付ける。{title, score, stars, why, explore}。"""
        rng = rng or random.Random()
        out = []
        for i, m in enumerate(movies, 1):
            feats = features(m, i)
            p, parts = self.score(feats)
            stars = "★★" if p >= STAR2 else "★" if p >= STAR1 else ""
            out.append({"title": m.get("title", ""), "score": p, "stars": stars, "why": why_text(parts), "explore": False,
                        "unknown": self.is_unknown(feats)})
        if explore and self.learned() and rng.random() < EPSILON:
            unknown = [o for o in out if o["unknown"] and not o["stars"]]
            if unknown:
                pick = rng.choice(unknown)
                pick["explore"] = True
                pick["stars"] = "🎲"
                pick["why"] = "試しに (まだ分からない物)"
        return out


def why_text(parts: list[tuple[str, float]]) -> str:
    if not parts:
        return ""
    def name(f: str) -> str:
        k, _, v = f.partition(":")
        return {"dir": "監督 " + v, "tok": f"語『{v}』", "rank": {"top10": "人気 10 位内", "top30": "人気 30 位内", "rest": "人気 31 位以下"}.get(v, v)}.get(k, f)
    return "、".join(f"{name(f)} {v:+.1f}" for f, v in parts)


def feature_label(f: str) -> str:
    return why_text([(f, 0.0)]).rsplit(" ", 1)[0]


# ---- 評価 (指標を疑う: 無作為と『rank だけ』と『乱数の w』を並べる) ----
def leave_one_out(watchlist: list[dict], now_showing: list[dict], variant: str = "model", seed: int = 0) -> dict:
    """WL の各 1 件を抜いて残りで学習し、抜いた物 (上映中にある物だけ) の順位を測る。
    variant: model | rank_only | random_w | random。戻り {n, mean_rank, N, ranks}。"""
    titles = [m.get("title", "") for m in now_showing]
    idx = {t: i for i, t in enumerate(titles)}
    rng = random.Random(seed)
    ranks = []
    for k, held in enumerate(watchlist):
        if held.get("title") not in idx:
            continue
        m = Model()
        for j, w in enumerate(watchlist):
            if j == k:
                continue
            r = idx.get(w.get("title"))
            feats = features(w, (r + 1) if r is not None else None)
            if variant == "rank_only":
                feats = [f for f in feats if f.startswith("rank:")]
            m.update(feats, REWARDS["wl"])
        if variant == "random_w":
            for f in list(m.w):
                m.w[f] = rng.uniform(-1, 1)
        scored = []
        for i, mv in enumerate(now_showing, 1):
            feats = features(mv, i)
            if variant == "rank_only":
                feats = [f for f in feats if f.startswith("rank:")]
            p, _ = m.score(feats)
            if variant == "random":
                p = rng.random()
            scored.append((p, mv.get("title")))
        scored.sort(key=lambda x: -x[0])
        ranks.append(next(i for i, (_, t) in enumerate(scored, 1) if t == held.get("title")))
    return {"n": len(ranks), "N": len(now_showing), "mean_rank": (sum(ranks) / len(ranks)) if ranks else None, "ranks": ranks}
