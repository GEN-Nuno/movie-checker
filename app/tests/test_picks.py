"""『おすすめ』の選び方 (MV-9)。ネット不要。"""
import random
from datetime import datetime

from moviecheck import picks, taste


def _movies():
    return [
        {"title": "大作", "movie_id": "1", "director": "A", "synopsis": "壮大なアクション 戦い", "cast": ["x"]},
        {"title": "泣ける", "movie_id": "2", "director": "B", "synopsis": "家族の絆と涙", "cast": ["y"]},
        {"title": "笑える", "movie_id": "3", "director": "C", "synopsis": "爆笑コメディ", "cast": ["z"]},
        {"title": "A の別作", "movie_id": "4", "director": "A", "synopsis": "", "cast": []},
        {"title": "静か", "movie_id": "5", "director": "D", "synopsis": "日常を描くドラマ", "cast": []},
    ]


def test_mood_filter_keeps_matching_and_falls_back():
    ms = _movies()
    kept, note = picks.mood_filter(ms, "笑いたい")
    assert [m["title"] for m in kept] == ["笑える"] and note == ""
    kept, note = picks.mood_filter([ms[4]], "迫力")
    assert len(kept) == 1 and "おまかせ" in note
    assert picks.mood_filter(ms, "おまかせ") == (ms, "")


def test_choose_wl_first_then_taste_then_explore_no_same_director(tmp_path):
    ms = _movies()
    m = taste.Model(tmp_path / "t.json")
    for _ in range(4):
        m.feedback_on({"title": "静か", "director": "D"}, "good")
    wl = picks.wl_showing_titles([{"title": "泣ける"}], [{"name": "A", "movies": [{"movie_id": "1"}]}], {x["title"]: x for x in ms})
    assert wl == {"泣ける": "観たい映画で上映中", "大作": "監督 A の作品"}
    got = picks.choose(ms, wl, m, dismissed=set(), rng=random.Random(0))
    kinds = [g["kind"] for g in got]
    titles = [g["movie"]["title"] for g in got]
    assert len(got) == 3 and titles[0] == "大作" and titles[1] == "泣ける"
    assert "A の別作" not in titles                      # 同じ監督は 1 枚
    assert kinds[2] in ("taste", "explore", "rank")
    got2 = picks.choose(ms, wl, m, dismissed={"1"}, rng=random.Random(0))
    assert "大作" not in [g["movie"]["title"] for g in got2]   # 当日の『いまいち』


def test_choose_without_learning_uses_rank_order():
    ms = _movies()
    got = picks.choose(ms, {}, taste.Model(), dismissed=set(), rng=random.Random(0))
    assert [g["movie"]["title"] for g in got] == ["大作", "泣ける", "笑える"]
    assert got[0]["reason"] == "人気 1 位"


def test_dismissed_state_resets_by_date(tmp_path):
    assert picks.load_dismissed(tmp_path, "2026-10-30") == set()
    assert picks.add_dismissed(tmp_path, "2026-10-30", "7") == {"7"}
    assert picks.load_dismissed(tmp_path, "2026-10-30") == {"7"}
    assert picks.load_dismissed(tmp_path, "2026-10-31") == set()


def test_afterglow_counts_recent_positive_feedback():
    ns = {x["title"]: x for x in _movies()}
    now = datetime(2026, 10, 30, 20, 0)
    fb = [
        {"at": "2026-10-29 10:00", "title": "大作", "kind": "wl", "r": 1.0},
        {"at": "2026-10-28 10:00", "title": "A の別作", "kind": "good", "r": 2.0},
        {"at": "2026-10-01 10:00", "title": "泣ける", "kind": "good", "r": 2.0},     # 古い
        {"at": "2026-10-29 11:00", "title": "静か", "kind": "skip", "r": -1.0},      # 負
    ]
    assert picks.afterglow(fb, ns, now) == "今週: 監督 A に惹かれています (観たい 1・👍 1)"
    assert picks.afterglow(fb[:1], ns, now) == "今週: 1 本に反応 (観たい 1)"
    assert picks.afterglow([], ns, now) == ""


def test_mood_counts_and_word_counts():
    ms = _movies()
    c = picks.mood_counts(ms)
    assert c["おまかせ"] == 5 and c["笑いたい"] == 1 and c["迫力"] >= 1
    wc = dict(picks.word_counts(ms))
    assert wc.get("家族") == 1 and wc.get("コメディ") == 1


def test_choose_n_and_exclude_for_next_picks():
    """MV-20: n 枚と、『次の候補』の除外 (題名)。足りなければ足りない数で返す。"""
    ms = _movies()
    first = picks.choose(ms, {}, taste.Model(), dismissed=set(), rng=random.Random(0), n=2)
    assert [g["movie"]["title"] for g in first] == ["大作", "泣ける"]
    nxt = picks.choose(ms, {}, taste.Model(), dismissed=set(), rng=random.Random(0), n=2, exclude={"大作", "泣ける"})
    assert [g["movie"]["title"] for g in nxt] == ["笑える", "A の別作"]   # 大作が除外なので監督 A は空いている
    last = picks.choose(ms, {}, taste.Model(), dismissed=set(), rng=random.Random(0), n=2, exclude={"大作", "泣ける", "笑える", "A の別作"})
    assert [g["movie"]["title"] for g in last] == ["静か"]
