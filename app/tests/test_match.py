from moviecheck.match import find_match, normalize, ranked_suggestions, suggest_score


def test_normalize_kana_width_and_symbols():
    assert normalize("ｱｲｳ") == "アイウ"
    assert normalize("あいう") == "アイウ"
    assert normalize("名探偵コナン　100万ドルの五稜星") == normalize("名探偵コナン 100万ドルの五稜星")
    assert normalize("A・B：C") == "abc"


def test_find_match_order():
    movies = ["国宝", "国宝 (4K)", "ゴジラ-1.0"]
    assert find_match("国宝", movies) == "国宝"            # 完全一致が先
    assert find_match("ゴジラ", movies) == "ゴジラ-1.0"    # 題名が問いを含む
    assert find_match("ゴジラ-1.0 を観たい", movies) == "ゴジラ-1.0"  # 問いが題名 (3 字以上) を含む
    assert find_match("無い", movies) is None


def test_suggest_score_prefers_prefix_then_contains():
    exact = suggest_score("国宝", "国宝")
    prefix = suggest_score("国", "国宝")
    inside = suggest_score("宝", "国宝物語")
    assert exact == 1.0
    assert exact > prefix > inside > 0


def test_ranked_suggestions_sorted_and_limited():
    cands = ["国宝", "国宝物語", "宝島", "全く別"]
    got = ranked_suggestions("国宝", cands, max_items=2)
    assert [c for c, _ in got] == ["国宝", "国宝物語"]
    assert all(s > 0 for _, s in got)
