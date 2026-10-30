"""eiga.com の取得 (MV-1 で movie_app.py から分けた。挙動は v3.0 と同じ。
失敗は今までどおり空を返すが、last_error に『何が起きたか』を残す)。"""
from __future__ import annotations

import json
import re
import threading
from urllib.parse import quote as urlquote

import requests
from bs4 import BeautifulSoup

from .match import normalize

EIGA_NOW_URL = "https://eiga.com/now/"
EIGA_RANKING_URL = "https://eiga.com/ranking/"
EIGA_BASE = "https://eiga.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}
_local = threading.local()  # thread ごとの Session (MV-5: keep-alive)
SKIP_TEXTS = {"予告編・動画", "レビュー", "作品情報", "作品", "映画館を探す"}
MAX_BYTES = 3 * 1024 * 1024  # 応答はここで打ち切る (MV-3)

PREFECTURES = {
    "1": "北海道", "2": "青森", "3": "岩手", "4": "宮城", "5": "秋田",
    "6": "山形", "7": "福島", "8": "茨城", "9": "栃木", "10": "群馬",
    "11": "埼玉", "12": "千葉", "13": "東京", "14": "神奈川", "15": "新潟",
    "16": "富山", "17": "石川", "18": "福井", "19": "山梨", "20": "長野",
    "21": "岐阜", "22": "静岡", "23": "愛知", "24": "三重", "25": "滋賀",
    "26": "京都", "27": "大阪", "28": "兵庫", "29": "奈良", "30": "和歌山",
    "31": "鳥取", "32": "島根", "33": "岡山", "34": "広島", "35": "山口",
    "36": "徳島", "37": "香川", "38": "愛媛", "39": "高知", "40": "福岡",
    "41": "佐賀", "42": "長崎", "43": "熊本", "44": "大分", "45": "宮崎",
    "46": "鹿児島", "47": "沖縄",
}

# 直近の失敗。{"kind": "net" | "http" | "empty", "text": 人に見せる訳, "url": url}。成功で None に戻る
last_error: dict | None = None

_ID_RE = re.compile(r"^\d{1,12}$")


def error_text(max_len: int = 40) -> str:
    """状態欄に出す 1 行 (40 字まで)。失敗が無ければ空。"""
    if not last_error:
        return ""
    t = last_error["text"]
    return t if len(t) <= max_len else t[: max_len - 1] + "…"


def _set_error(kind: str, text: str, url: str) -> None:
    global last_error
    last_error = {"kind": kind, "text": text, "url": url}


def _session():
    """この thread の requests.Session (無ければ作る)。接続を使い回す (MV-5)。試験では差し替える。"""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        _local.session = s
    return s


def _drop_session():
    s = getattr(_local, "session", None)
    _local.session = None
    if s is not None:
        try:
            s.close()
        except Exception:
            pass


def _get(url: str, timeout: int, ok_statuses=(200,)) -> tuple[BeautifulSoup | None, int]:
    """GET して soup を返す。失敗は (None, status) で、last_error に訳を残す。status は 404 等、例外なら 0。"""
    global last_error
    try:
        resp = _session().get(url, headers=HEADERS, timeout=timeout, stream=True)
        status = resp.status_code
        if status not in ok_statuses:
            if status in (404, 410):
                _set_error("http", f"eiga.com に該当のページがありません ({status})", url)
            else:
                _set_error("http", f"eiga.com が {status} を返しました", url)
            resp.close()
            return None, status
        body = resp.raw.read(MAX_BYTES + 1, decode_content=True)
        resp.close()
        if len(body) > MAX_BYTES:
            _set_error("http", "eiga.com の応答が大きすぎます (3MB 超)", url)
            return None, status
        text = body.decode("utf-8", errors="replace")
    except requests.exceptions.SSLError:
        _set_error("net", "eiga.com との TLS で失敗 (証明書)", url)
        return None, 0
    except requests.exceptions.Timeout:
        _set_error("net", "eiga.com が時間内に応答しません", url)
        return None, 0
    except requests.exceptions.ConnectionError:
        _drop_session()  # 使い回した接続が駄目になったかもしれない。次の要求で作り直す
        _set_error("net", "eiga.com に繋がりません (ネット)", url)
        return None, 0
    except requests.RequestException as e:
        _set_error("net", f"eiga.com の取得に失敗 ({type(e).__name__})", url)
        return None, 0
    last_error = None
    return BeautifulSoup(text, "html.parser"), status


def _check_id(value: str, what: str) -> bool:
    """URL に入れる id は数字だけ (MV-3)。駄目なら last_error を立てる。"""
    if _ID_RE.match(str(value or "")):
        return True
    _set_error("http", f"{what} の形が正しくありません", "")
    return False


def fetch_now_showing(progress_cb=None) -> list[dict]:
    """上映中の映画 (全国ランキング順) を 1〜11 ページ取る。題名で重複を除く。"""
    movies = []
    for page in range(1, 12):
        if page == 1:
            url = f"{EIGA_NOW_URL}all/rank/"
        else:
            url = f"{EIGA_NOW_URL}all/rank/{page}/"
        soup, status = _get(url, 15)
        if soup is None:
            if status == 404 and page > 1:
                # 最後のページを越えた (正常)
                global last_error
                last_error = None
            break
        found = _extract_movies(soup)
        if not found:
            if page == 1:
                _set_error("empty", "eiga.com のページに映画が見つかりません (形が変わった?)", url)
            break
        movies.extend(found)
        if progress_cb:
            progress_cb(page, len(movies))
    seen = set()
    unique = []
    for m in movies:
        if m["title"] not in seen:
            seen.add(m["title"])
            unique.append(m)
    return unique


def fetch_ranking() -> list[dict]:
    soup, _ = _get(EIGA_RANKING_URL, 15)
    if soup is None:
        return []
    return _extract_movies(soup)


def _extract_movies(soup: BeautifulSoup) -> list[dict]:
    """eiga.com の一覧から {title, url, movie_id, director} を起こす。
    まず div.txt-box (監督つき)、無ければ a[href*="/movie/"] 全部から。"""
    movies, seen = [], set()
    for box in soup.select("div.txt-box"):
        a_tag = box.select_one('h2 a[href*="/movie/"]')
        if a_tag is None:
            a_tag = box.select_one('a[href*="/movie/"]')
        if a_tag is None:
            continue
        href = a_tag.get("href", "")
        text = a_tag.get_text(strip=True)
        if not re.match(r"^(https://eiga\.com)?/movie/\d+/$", href):
            continue
        if not text or len(text) <= 1 or text in SKIP_TEXTS or text in seen:
            continue
        seen.add(text)
        m = re.search(r"/movie/(\d+)/", href)
        movie_id = m.group(1) if m else ""
        full_url = href if href.startswith("http") else f"{EIGA_BASE}{href}"
        directors = []
        staff_ul = box.select_one("ul.cast-staff")
        if staff_ul:
            for li in staff_ul.select("li"):
                li_text = li.get_text()
                if "監督" in li_text:
                    for span in li.select("span"):
                        name = span.get_text(strip=True)
                        if name:
                            directors.append(name)
        director = ", ".join(directors)
        movies.append({"title": text, "url": full_url, "movie_id": movie_id, "director": director})
    for a_tag in soup.select('a[href*="/movie/"]'):
        href = a_tag.get("href", "")
        text = a_tag.get_text(strip=True)
        if not re.match(r"^(https://eiga\.com)?/movie/\d+/$", href):
            continue
        if not text or len(text) <= 1 or text in SKIP_TEXTS or text in seen:
            continue
        seen.add(text)
        m = re.search(r"/movie/(\d+)/", href)
        movie_id = m.group(1) if m else ""
        full_url = href if href.startswith("http") else f"{EIGA_BASE}{href}"
        movies.append({"title": text, "url": full_url, "movie_id": movie_id, "director": ""})
    return movies


def fetch_theaters(movie_id: str, pref_code: str) -> list[dict]:
    """その映画を上映している映画館 (都道府県ごと)。404 や失敗は空。"""
    if not (_check_id(movie_id, "movie_id") and _check_id(pref_code, "pref_code")):
        return []
    url = f"{EIGA_BASE}/movie-pref/{movie_id}/{pref_code}/"
    soup, status = _get(url, 15)
    if soup is None:
        if status in (404, 410):   # その県で上映が無い (2026-10 の実測: 410 が返る) は失敗ではない
            global last_error
            last_error = None
        return []
    theaters = []
    for h3_a in soup.select("h3 a"):
        href = h3_a.get("href", "")
        name = h3_a.get_text(strip=True)
        if "/movie-theater/" in href and name:
            full_url = href if href.startswith("http") else f"{EIGA_BASE}{href}"
            theaters.append({"name": name, "url": full_url})
    return theaters


def fetch_theaters_multi(movie_id: str, pref_codes: list[str]) -> dict[str, list[dict]]:
    result = {}
    for code in pref_codes:
        name = PREFECTURES.get(code, code)
        theaters = fetch_theaters(movie_id, code)
        if theaters:
            result[name] = theaters
    return result


def search_eiga(query: str, max_pages: int = 2) -> list[dict]:
    """eiga.com の検索 (作品) を max_pages ページまで。"""
    encoded = urlquote(query, safe="")
    movies = []
    seen = set()
    for page in range(1, max_pages + 1):
        suffix = f"{page}/" if page > 1 else ""
        url = f"{EIGA_BASE}/search/{encoded}/movie/{suffix}"
        soup, _ = _get(url, 10)
        if soup is None:
            return movies
        page_count = 0
        for a_tag in soup.select('a[href*="/movie/"]'):
            href = a_tag.get("href", "")
            text = a_tag.get_text(strip=True)
            if not re.match(r"^(https://eiga\.com)?/movie/\d+/$", href):
                continue
            if not text or len(text) <= 1 or text in SKIP_TEXTS or text in seen:
                continue
            seen.add(text)
            m = re.search(r"/movie/(\d+)/", href)
            movie_id = m.group(1) if m else ""
            full_url = href if href.startswith("http") else f"{EIGA_BASE}{href}"
            movies.append({"title": text, "url": full_url, "movie_id": movie_id})
            page_count += 1
        if page_count == 0:
            return movies
    return movies


def search_persons(query: str, max_items: int = 10) -> list[str]:
    """eiga.com の検索から人物名を max_items 件まで (監督名のオートコンプリート用)。"""
    encoded = urlquote(query, safe="")
    url = f"{EIGA_BASE}/search/{encoded}/"
    soup, _ = _get(url, 10)
    if soup is None:
        return []
    names = []
    seen = set()
    for a_tag in soup.select('a[href*="/person/"]'):
        href = a_tag.get("href", "")
        text = a_tag.get_text(strip=True)
        if not re.match(r"^(https://eiga\.com)?/person/\d+/$", href):
            continue
        if not text or len(text) <= 1 or text in seen:
            continue
        seen.add(text)
        names.append(text)
        if len(names) >= max_items:
            return names
    return names


def fetch_movie_director(movie_id: str) -> str:
    """作品ページの ld+json (director) か ul.cast-staff から監督名。"""
    if not _check_id(movie_id, "movie_id"):
        return ""
    url = f"{EIGA_BASE}/movie/{movie_id}/"
    soup, _ = _get(url, 10)
    if soup is None:
        return ""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if "director" in data:
                dirs = data["director"]
                if isinstance(dirs, list):
                    return ", ".join(d.get("name", "") for d in dirs if d.get("name"))
                if isinstance(dirs, dict) and dirs.get("name"):
                    return dirs["name"]
        except Exception:
            continue
    staff_ul = soup.select_one("ul.cast-staff")
    if staff_ul:
        names = []
        for li in staff_ul.select("li"):
            if "監督" in li.get_text():
                for sp in li.select("span"):
                    n = sp.get_text(strip=True)
                    if n:
                        names.append(n)
        if names:
            return ", ".join(names)
    return ""


def fetch_person_id(name: str) -> str:
    """人名の検索から person id。正規化した名前を含む物を優先、無ければ最初の物。"""
    encoded = urlquote(name, safe="")
    url = f"{EIGA_BASE}/search/{encoded}/"
    soup, _ = _get(url, 10)
    if soup is None:
        return ""
    nq = normalize(name)
    first_pid = ""
    for a_tag in soup.select('a[href*="/person/"]'):
        href = a_tag.get("href", "")
        text = a_tag.get_text(strip=True)
        if not re.match(r"^(https://eiga\.com)?/person/\d+/$", href):
            continue
        m = re.search(r"/person/(\d+)/", href)
        if not m:
            continue
        pid = m.group(1)
        if not first_pid:
            first_pid = pid
        if text and nq in normalize(text):
            return pid
    return first_pid


def fetch_directed_movies(person_id: str, max_pages: int = 20) -> list[dict]:
    """人物ページの作品一覧 (p.label に役割、p.title に題名)。監督の役割が無い物は除く。"""
    if not _check_id(person_id, "person_id"):
        return []
    movies = []
    seen = set()
    for page in range(1, max_pages + 1):
        suffix = f"{page}/" if page > 1 else ""
        url = f"{EIGA_BASE}/person/{person_id}/movie/{suffix}"
        soup, _ = _get(url, 10)
        if soup is None:
            return movies
        page_count = 0
        for a_tag in soup.select('a[href*="/movie/"]'):
            href = a_tag.get("href", "")
            if not re.match(r"^(https://eiga\.com)?/movie/\d+/$", href):
                continue
            label_p = a_tag.select_one("p.label")
            title_p = a_tag.select_one("p.title")
            if not title_p:
                continue
            role_text = label_p.get_text(strip=True) if label_p else ""
            title = title_p.get_text(strip=True)
            if label_p and "監督" not in role_text:
                continue
            if not title or title in seen:
                continue
            seen.add(title)
            m = re.search(r"/movie/(\d+)/", href)
            movie_id = m.group(1) if m else ""
            full_url = href if href.startswith("http") else f"{EIGA_BASE}{href}"
            movies.append({"title": title, "movie_id": movie_id, "url": full_url, "role": role_text})
            page_count += 1
        if page_count == 0:
            return movies
    return movies
