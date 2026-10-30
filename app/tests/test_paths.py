"""置き場の決め方と、以前の json の取り込み (MV-4)。ネット不要。"""
import json

from moviecheck import paths


def test_source_run_uses_py_dir(tmp_path):
    assert paths.data_dir(tmp_path, frozen=False, appdata=str(tmp_path / "ad")) == tmp_path


def test_frozen_always_uses_appdata_even_with_json_beside_exe(tmp_path):
    (tmp_path / "watchlist.json").write_text("[]", encoding="utf-8")
    assert paths.data_dir(tmp_path, frozen=True, appdata=str(tmp_path / "ad")) == tmp_path / "ad" / paths.APP_NAME


def test_is_empty_counts_any_data_file(tmp_path):
    assert paths.is_empty(tmp_path)
    (tmp_path / "director_watchlist.json").write_text("[]", encoding="utf-8")
    assert not paths.is_empty(tmp_path) and paths.has_data(tmp_path)


def test_frozen_without_json_goes_to_appdata_and_creates_it(tmp_path):
    ad = tmp_path / "ad"
    got = paths.data_dir(tmp_path / "exe", frozen=True, appdata=str(ad))
    assert got == ad / paths.APP_NAME
    assert got.is_dir()
    assert paths.is_empty(got)


def test_frozen_without_appdata_falls_back_to_exe_dir(tmp_path):
    assert paths.data_dir(tmp_path, frozen=True, appdata="") == tmp_path


def test_import_copies_valid_json_and_skips_broken(tmp_path):
    src = tmp_path / "old"
    dst = tmp_path / "new"
    src.mkdir()
    dst.mkdir()
    (src / "watchlist.json").write_text(json.dumps([{"title": "国宝"}]), encoding="utf-8")
    (src / "settings.json").write_text('{"pref_codes": ["23"]}', encoding="utf-8")
    (src / "movie_cache.json").write_text("{broken", encoding="utf-8")
    (src / "other.txt").write_text("x", encoding="utf-8")
    copied, notes = paths.import_from(src, dst)
    assert copied == ["watchlist.json", "settings.json"]
    assert not (dst / "movie_cache.json").exists()
    assert notes and "movie_cache.json" in notes[0]
    assert (src / "movie_cache.json").exists()  # 元は触らない
    assert json.loads((dst / "watchlist.json").read_text(encoding="utf-8"))[0]["title"] == "国宝"
    assert not paths.is_empty(dst)


def test_import_same_dir_does_nothing(tmp_path):
    copied, notes = paths.import_from(tmp_path, tmp_path)
    assert copied == [] and notes == ["同じフォルダです"]


def test_build_info_missing_is_empty(tmp_path):
    assert paths.build_info(tmp_path) == {}
    (tmp_path / "build_info.json").write_text('{"built": "2026-10-30 17:00"}', encoding="utf-8")
    assert paths.build_info(tmp_path)["built"] == "2026-10-30 17:00"
