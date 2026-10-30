"""requests を差し替えて、ネット無しで取り出しと失敗の訳を試す。"""
import io

import pytest
import requests
from types import SimpleNamespace

from moviecheck import fetch

NOW_HTML = """
<html><body>
<div class="txt-box"><h2><a href="/movie/100001/">国宝</a></h2>
  <ul class="cast-staff"><li>監督<span>李相日</span></li></ul></div>
<div class="txt-box"><h2><a href="/movie/100002/">ゴジラ-1.0</a></h2></div>
<a href="/movie/100003/">レビュー</a>
<a href="https://eiga.com/movie/100004/">鬼滅の刃</a>
</body></html>
"""
THEATER_HTML = '<h3><a href="/movie-theater/13/130201/3001/">TOHOシネマズ 新宿</a></h3><h3><a href="/x/">無関係</a></h3>'
PERSON_HTML = '<a href="/person/501/">李相日</a><a href="/person/502/">李相日 (別)</a><a href="/person/503/">山田</a>'
DIRECTED_HTML = """
<a href="/movie/1/"><p class="label">監督</p><p class="title">A</p></a>
<a href="/movie/2/"><p class="label">出演</p><p class="title">B</p></a>
<a href="/movie/3/"><p class="title">C</p></a>
"""
LD_HTML = '<script type="application/ld+json">{"director": [{"name": "李相日"}, {"name": "X"}]}</script>'


class FakeRaw(io.BytesIO):
    def read(self, n=-1, decode_content=None):  # urllib3 の raw.read と同じ形
        return super().read(n)


class FakeResp:
    def __init__(self, status, html=""):
        self.status_code = status
        self.html = html
        self.raw = FakeRaw(html.encode("utf-8"))

    def close(self):
        pass


class Fake:
    """url → FakeResp か例外。無い url は 404。"""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append(url)
        r = self.table.get(url, FakeResp(404))
        if isinstance(r, Exception):
            raise r
        return FakeResp(r.status_code, r.html)  # 毎回読み直せるように新しく


@pytest.fixture
def fake(monkeypatch):
    def install(table):
        f = Fake(table)
        monkeypatch.setattr(fetch, "_session", lambda: SimpleNamespace(get=f))  # MV-5: Session を差し替える
        return f
    fetch.last_error = None
    return install


def test_now_showing_extracts_titles_and_directors(fake):
    f = fake({"https://eiga.com/now/all/rank/": FakeResp(200, NOW_HTML)})
    got = fetch.fetch_now_showing()
    assert [m["title"] for m in got] == ["国宝", "ゴジラ-1.0", "鬼滅の刃"]
    assert got[0]["director"] == "李相日" and got[0]["movie_id"] == "100001"
    assert got[2]["url"] == "https://eiga.com/movie/100004/"
    assert f.calls == ["https://eiga.com/now/all/rank/", "https://eiga.com/now/all/rank/2/", "https://eiga.com/now/all/release/"]   # MV-19: 全作品も 1 回見に行く
    assert fetch.last_error is None  # 2 ページ目の 404 は正常


def test_now_showing_net_error_leaves_reason(fake):
    fake({"https://eiga.com/now/all/rank/": requests.exceptions.ConnectionError("x")})
    assert fetch.fetch_now_showing() == []
    assert fetch.last_error["kind"] == "net"
    assert "繋がりません" in fetch.error_text()


def test_now_showing_changed_html_leaves_reason(fake):
    fake({"https://eiga.com/now/all/rank/": FakeResp(200, "<html><body>nothing</body></html>")})
    assert fetch.fetch_now_showing() == []
    assert fetch.last_error["kind"] == "empty"


def test_http_500_and_timeout_texts(fake):
    fake({"https://eiga.com/ranking/": FakeResp(500)})
    assert fetch.fetch_ranking() == []
    assert "500" in fetch.error_text()
    fake({"https://eiga.com/ranking/": requests.exceptions.Timeout()})
    fetch.fetch_ranking()
    assert "時間内" in fetch.error_text()


def test_error_text_is_cut_to_40():
    fetch._set_error("net", "あ" * 60, "")
    assert len(fetch.error_text()) == 40
    fetch.last_error = None
    assert fetch.error_text() == ""


def test_too_large_body_is_refused(fake):
    fake({"https://eiga.com/ranking/": FakeResp(200, "x" * (fetch.MAX_BYTES + 10))})
    assert fetch.fetch_ranking() == []
    assert "3MB" in fetch.error_text()


def test_theaters_and_id_check(fake):
    f = fake({"https://eiga.com/movie-pref/100001/13/": FakeResp(200, THEATER_HTML)})
    got = fetch.fetch_theaters_multi("100001", ["13", "27"])
    assert got == {"東京": [{"name": "TOHOシネマズ 新宿", "url": "https://eiga.com/movie-theater/13/130201/3001/"}]}
    assert fetch.fetch_theaters("../etc", "13") == []
    assert "movie_id" in fetch.error_text()
    assert all("etc" not in u for u in f.calls)


def test_person_id_prefers_normalized_match(fake):
    fake({"https://eiga.com/search/%E6%9D%8E%E7%9B%B8%E6%97%A5/": FakeResp(200, PERSON_HTML)})
    assert fetch.fetch_person_id("李相日") == "501"
    assert fetch.search_persons("李相日") == ["李相日", "李相日 (別)", "山田"]


def test_directed_movies_keeps_director_role_only(fake):
    fake({"https://eiga.com/person/501/movie/": FakeResp(200, DIRECTED_HTML)})
    got = fetch.fetch_directed_movies("501")
    assert [m["title"] for m in got] == ["A", "C"]


def test_movie_director_from_ld_json(fake):
    fake({"https://eiga.com/movie/100001/": FakeResp(200, LD_HTML)})
    assert fetch.fetch_movie_director("100001") == "李相日, X"


def test_theaters_410_means_not_showing_there(fake):
    fake({"https://eiga.com/movie-pref/100001/13/": FakeResp(410, "<html>gone</html>")})
    assert fetch.fetch_theaters("100001", "13") == []
    assert fetch.last_error is None


# ---- MV-5: thread ごとの Session を使い回し、ConnectionError で捨てる ----
def test_session_is_reused_in_same_thread_and_separate_per_thread():
    import threading
    fetch._drop_session()
    a = fetch._session()
    assert fetch._session() is a
    other = []
    t = threading.Thread(target=lambda: other.append(fetch._session()))
    t.start()
    t.join()
    assert other and other[0] is not a
    assert a.headers["User-Agent"] == fetch.USER_AGENT
    fetch._drop_session()
    assert fetch._session() is not a


def test_connection_error_drops_session(monkeypatch):
    fetch._drop_session()
    s = fetch._session()
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("x")
    monkeypatch.setattr(s, "get", boom)
    soup, status = fetch._get("https://eiga.com/now/all/rank/", 5)
    assert soup is None and status == 0
    assert fetch.last_error["kind"] == "net"
    assert fetch._session() is not s


# ---- MV-11: 劇場の 1 日キャッシュ ----
THEATER_HTML2 = '<h3><a href="/movie-theater/1/">シネマA</a></h3><h3><a href="/movie-theater/2/">シネマB</a></h3>'


def test_theaters_cached_reads_once_per_day_and_caches_empty(tmp_path, fake):
    from datetime import datetime
    url = "https://eiga.com/movie-pref/5/13/"
    f = fake({url: FakeResp(200, THEATER_HTML2), "https://eiga.com/movie-pref/5/23/": FakeResp(410, "")})
    d1 = datetime(2026, 10, 30, 10, 0)
    got = fetch.theaters_cached("5", "13", tmp_path, now=d1)
    assert [t["name"] for t in got] == ["シネマA", "シネマB"]
    assert fetch.theaters_cached("5", "13", tmp_path, now=d1) == got and f.calls.count(url) == 1
    assert fetch.theaters_cached("5", "23", tmp_path, now=d1) == [] and fetch.theaters_cached("5", "23", tmp_path, now=d1) == []
    assert f.calls.count("https://eiga.com/movie-pref/5/23/") == 1          # 空 (410) もキャッシュ
    fetch.theaters_cached("5", "13", tmp_path, now=datetime(2026, 10, 31, 10, 0))
    assert f.calls.count(url) == 2                                              # 日付が変わると取り直す
    assert fetch.fetch_theaters_multi("5", ["13"], tmp_path) == {"東京": got}
    fetch.clear_theaters_cache(tmp_path)
    assert not (tmp_path / "theaters_cache.json").exists()


def test_theaters_cached_does_not_cache_network_failure(tmp_path, fake):
    f = fake({"https://eiga.com/movie-pref/5/13/": requests.exceptions.ConnectionError("x")})
    assert fetch.theaters_cached("5", "13", tmp_path) == []
    assert not (tmp_path / "theaters_cache.json").exists()
    assert len(f.calls) == 1


# ---- MV-19: 館の一覧・館の作品・全作品の合流 ----
THEATER_LIST_HTML = """<dl class="theater-area-list"><dt><h3>渋谷の映画館</h3></dt><dd><ul>
<li><a href="/theater/13/130301/3028/">シアター・イメージフォーラム</a> （7）</li>
<li><a href="/theater/13/130301/3031/">ユーロスペース</a> （14）</li><li><a href="/theater/">全部</a></li></ul></dd></dl>
<dl class="theater-area-list"><dt><h3>新宿の映画館</h3></dt><dd><ul><li><a href="/theater/13/130401/3002/">テアトル新宿</a> （3）</li></ul></dd></dl>"""
THEATER_PAGE_HTML = """<div class="theater-movie"><ul class="slide-menu slide-10">
<li><a class="smooth-scroll" href="#m106743"><img alt="京都ALS患者嘱託殺人事件" src="https://media.eiga.com/images/movie/106743/photo/x/160.jpg"></a></li>
<li><a class="smooth-scroll" href="#m105451"><img alt="もしもの愛" src="https://evil.example/x.jpg"></a></li>
<li><a class="smooth-scroll" href="#m106743"><img alt="重複"></a></li></ul></div>"""


def test_theater_list_and_movies(fake, tmp_path):
    f = fake({"https://eiga.com/theater/13/": FakeResp(200, THEATER_LIST_HTML),
              "https://eiga.com/theater/13/130301/3031/": FakeResp(200, THEATER_PAGE_HTML)})
    lst = fetch.fetch_theater_list("13")
    assert [(t["name"], t["area"], t["count"]) for t in lst] == [("シアター・イメージフォーラム", "渋谷", 7), ("ユーロスペース", "渋谷", 14), ("テアトル新宿", "新宿", 3)]
    assert lst[1]["url"] == "https://eiga.com/theater/13/130301/3031/"
    mv = fetch.fetch_theater_movies(lst[1]["url"])
    assert [(m["title"], m["movie_id"]) for m in mv] == [("京都ALS患者嘱託殺人事件", "106743"), ("もしもの愛", "105451")]
    assert mv[0]["image"].startswith("https://media.eiga.com/") and mv[1]["image"] == ""
    assert fetch.fetch_theater_movies("https://evil.example/theater/13/1/1/") == [] and "url" in fetch.error_text()
    assert fetch.fetch_theater_list("../x") == []
    # 1 日キャッシュ
    assert fetch.theater_list_cached("13", tmp_path) == lst and fetch.theater_list_cached("13", tmp_path) == lst
    assert f.calls.count("https://eiga.com/theater/13/") == 2   # 1 回目 (直) + キャッシュの 1 回目
    assert fetch.theater_movies_cached(lst[1]["url"], tmp_path) == mv and f.calls.count(lst[1]["url"]) == 2


def test_now_showing_adds_release_pages(fake):
    f = fake({"https://eiga.com/now/all/rank/": FakeResp(200, NOW_HTML),
              "https://eiga.com/now/all/rank/2/": FakeResp(404, ""),
              "https://eiga.com/now/all/release/": FakeResp(200, NOW_HTML),
              "https://eiga.com/now/all/release/2/": FakeResp(404, "")})
    got = fetch.fetch_now_showing()
    titles = [m["title"] for m in got]
    assert len(titles) == 3 and len(titles) == len(set(titles))   # 同じ 3 本が重複しない
    assert fetch.last_error is None
    assert "https://eiga.com/now/all/release/" in f.calls
