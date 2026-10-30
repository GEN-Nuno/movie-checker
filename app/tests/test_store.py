import json

from moviecheck import store


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "watchlist.json"
    store.save_json(p, [{"title": "国宝"}])
    assert store.load_json(p) == [{"title": "国宝"}]
    assert not p.with_suffix(".json.tmp").exists()
    assert not p.with_suffix(".json.bak").exists()  # 初回は前の物が無い


def test_save_keeps_one_backup(tmp_path):
    p = tmp_path / "settings.json"
    store.save_json(p, {"v": 1})
    store.save_json(p, {"v": 2})
    assert json.loads(p.with_suffix(".json.bak").read_text(encoding="utf-8")) == {"v": 1}
    assert store.load_json(p) == {"v": 2}


def test_broken_file_restored_from_backup(tmp_path):
    p = tmp_path / "watchlist.json"
    store.save_json(p, [{"title": "a"}])
    store.save_json(p, [{"title": "a"}, {"title": "b"}])
    p.write_text("{broken", encoding="utf-8")
    assert store.load_json(p) == [{"title": "a"}]
    assert "退避" in store.last_notes["watchlist.json"]


def test_broken_file_without_backup_becomes_empty(tmp_path):
    p = tmp_path / "watchlist.json"
    p.write_text("{broken", encoding="utf-8")
    assert store.load_json(p) == []
    assert "空にしました" in store.last_notes["watchlist.json"]
    q = tmp_path / "settings.json"
    q.write_text("[", encoding="utf-8")
    assert store.load_json(q) == {}


def test_missing_file_is_empty_and_clears_note(tmp_path):
    p = tmp_path / "movie_cache.json"
    assert store.load_json(p) == {}
    assert "movie_cache.json" not in store.last_notes
