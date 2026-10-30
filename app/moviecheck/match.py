"""文字の正規化と照合、候補の点数 (MV-1 で movie_app.py から分けた。挙動は v3.0 と同じ)。"""
from __future__ import annotations

import math
import re
import unicodedata

_KANA_MAP = str.maketrans(
    "ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをんゔゕゖ",
    "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶ",
)


def normalize(text: str) -> str:
    """NFKC → ひらがなをカタカナに → 空白・記号を除く → 小文字。"""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_KANA_MAP)
    text = re.sub(r"[\s　・：:；;,，.。\-\−\–\—～〜/／]+", "", text)
    return text.lower().strip()


def find_match(query: str, movies) -> str | None:
    """完全一致 → 題名が問いを含む → 問いが題名 (3 字以上) を含む、の順で探す。"""
    q = normalize(query)
    for t in movies:
        if normalize(t) == q:
            return t
    for t in movies:
        if q in normalize(t):
            return t
    for t in movies:
        if normalize(t) in q and len(normalize(t)) >= 3:
            return t
    return None


def _common_subsequence_ratio(a: str, b: str) -> float:
    """最長共通部分列の長さ / 長い方の長さ。"""
    if not a or not b:
        return 0.0
    m, n = len(a), len(b)
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, [0] * (n + 1)
    return prev[n] / max(m, n)


def _bigram_similarity(a: str, b: str) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    bg_a = {a[i:i + 2] for i in range(len(a) - 1)}
    bg_b = {b[i:i + 2] for i in range(len(b) - 1)}
    inter = len(bg_a & bg_b)
    union = len(bg_a | bg_b)
    return inter / union if union else 0.0


def suggest_score(query: str, candidate: str, alpha: float = 3.0) -> float:
    """問いと候補の近さ (0〜1)。exp(-alpha × 距離)。"""
    nq = normalize(query)
    nc = normalize(candidate)
    if not nq:
        return 0.0
    if nq == nc:
        return math.exp(-alpha * 0.0)
    if nc.startswith(nq):
        ratio = len(nq) / len(nc)
        return math.exp(-alpha * (0.1 * (1 - ratio)))
    if nq.startswith(nc):
        return math.exp(-alpha * 0.15)
    pos = nc.find(nq)
    if pos >= 0:
        position_ratio = pos / max(len(nc) - len(nq), 1)
        return math.exp(-alpha * (0.2 + 0.3 * position_ratio))
    if nc in nq and len(nc) >= 2:
        return math.exp(-alpha * 0.4)
    bg_sim = _bigram_similarity(nq, nc)
    if bg_sim >= 0.25:
        return math.exp(-alpha * (0.25 + 0.45 * (1 - bg_sim)))
    lcs_ratio = _common_subsequence_ratio(nq, nc)
    if lcs_ratio >= 0.4:
        return math.exp(-alpha * (0.35 + 0.5 * (1 - lcs_ratio)))
    if len(nq) == 1 and nq in nc:
        pos1 = nc.find(nq)
        return math.exp(-alpha * (0.3 + 0.4 * (pos1 / max(len(nc) - 1, 1))))
    return 0.0


def ranked_suggestions(query: str, candidates, threshold: float = 0.08, max_items: int = 10, alpha: float = 3.0):
    scored = []
    for c in candidates:
        sc = suggest_score(query, c, alpha=alpha)
        if sc >= threshold:
            scored.append((c, sc))
    scored.sort(key=lambda x: -x[1])
    return scored[:max_items]
