"""好みの学習 (MV-8): 特徴・更新・星・探索・忘れる・1 つ抜き。ネット不要。"""
import random

from moviecheck import taste


def test_features_director_tokens_rank():
    f = taste.features({"title": "怪物の木こり 2", "director": "是枝裕和"}, 5)
    assert "dir:是枝裕和" in f and "tok:怪物" in f and "tok:木こり" not in f and "rank:top10" in f
    assert taste.features({"title": "ABC Movie", "director": ""}, 40) == ["tok:abc", "tok:movie", "rank:rest"]


def test_update_moves_weights_toward_reward_and_clips(tmp_path):
    m = taste.Model(tmp_path / "taste.json")
    feats = ["dir:A", "tok:x"]
    p0 = m.update(feats, 1.0)
    assert p0 == 0.0 and m.w["dir:A"] > 0
    for _ in range(200):
        m.update(feats, 2.0)
    assert all(abs(v) <= taste.CLIP for v in m.w.values())
    p, parts = m.score(feats)
    assert p > 0 and parts[0][0] in feats
    m.feedback_on({"title": "x", "director": "A"}, "bad")
    assert m.tally() == {"up": 0, "down": 1, "n": 1}
    m2 = taste.Model(tmp_path / "taste.json")
    assert m2.feedback and m2.w == m.w


def test_score_all_stars_and_explore(tmp_path):
    m = taste.Model(tmp_path / "t.json")
    for _ in range(5):
        m.feedback_on({"title": "海街", "director": "是枝裕和"}, "good")
    movies = [{"title": "怪物", "director": "是枝裕和"}, {"title": "未知の映画", "director": "誰か"}, {"title": "別の未知", "director": ""}]
    out = m.score_all(movies, rng=random.Random(1), explore=False)
    assert out[0]["stars"] in ("★", "★★") and "監督 是枝裕和" in out[0]["why"]
    assert out[1]["stars"] == "" and out[1]["unknown"]
    # ε の当たりを引くまで回すと、未知の物が 1 件だけ『試しに』になる
    hit = None
    for seed in range(50):
        o = m.score_all(movies, rng=random.Random(seed))
        ex = [x for x in o if x["explore"]]
        if ex:
            hit = ex
            break
    assert hit and len(hit) == 1 and hit[0]["stars"] == "🎲"
    assert taste.Model().score_all(movies)[0]["stars"] == ""   # 何も学んでいなければ星なし


def test_forget_and_top(tmp_path):
    m = taste.Model(tmp_path / "t.json")
    m.feedback_on({"title": "A", "director": "好き"}, "good")
    m.feedback_on({"title": "B", "director": "嫌い"}, "skip")
    likes, dislikes = m.top()
    assert likes[0][0] == "dir:好き" and dislikes[0][0] == "dir:嫌い"
    m.forget("dir:嫌い")
    assert "dir:嫌い" not in m.w and "dir:嫌い" not in taste.Model(tmp_path / "t.json").w
    assert taste.feature_label("dir:是枝裕和") == "監督 是枝裕和"


def test_leave_one_out_beats_random_on_toy_data():
    showing = [{"title": f"映画{i}", "director": "他人" + str(i)} for i in range(20)]
    showing += [{"title": f"是枝作{i}", "director": "是枝裕和"} for i in range(3)]
    wl = [{"title": f"是枝作{i}", "director": "是枝裕和"} for i in range(3)]
    r = taste.leave_one_out(wl, showing, "model")
    rnd = taste.leave_one_out(wl, showing, "random", seed=3)
    assert r["n"] == 3 and r["mean_rank"] <= 3 and rnd["mean_rank"] > r["mean_rank"]
