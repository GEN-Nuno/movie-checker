"""メール通知: 変化の検出・文面・送る/送らないの判断。SMTP も keyring も差し替え (ネット無し)。"""
from datetime import datetime

import pytest

from moviecheck import notify
from moviecheck.store import save_json

NOW = {"国宝": {"title": "国宝", "movie_id": "100001", "url": "https://eiga.com/movie/100001/", "director": "李相日"},
       "ゴジラ-1.0": {"title": "ゴジラ-1.0", "movie_id": "100002", "url": "https://eiga.com/movie/100002/", "director": "山崎貴"},
       "別の映画": {"title": "別の映画", "movie_id": "100003", "url": "", "director": ""}}
WL = [{"title": "国宝"}, {"title": "無い映画"}]
DWL = [{"name": "山崎貴", "movies": [{"title": "ゴジラ-1.0", "movie_id": "100002"}, {"title": "昔の作品", "movie_id": "9"}]}]
CFG = {"to": "me@example.com", "host": "smtp.example.com", "port": 465, "user": "me@example.com", "mode": "ssl",
       "on_change": True, "weekly": {"enabled": True, "weekday": 5, "hour": 8}}


@pytest.fixture
def env(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(notify, "get_password", lambda user: "secret" if user == "me@example.com" else None)

    def fake_send(cfg, password, subject, text, html):
        assert password == "secret"
        assert "secret" not in text and "secret" not in html
        sent.append({"subject": subject, "text": text, "html": html})
        return ""
    return {"dir": tmp_path, "sent": sent, "send": fake_send}


def test_showing_items_from_both_lists():
    items = notify.showing_items(WL, DWL, NOW)
    assert [(i["title"], i["source"]) for i in items] == [("国宝", "観たい映画"), ("ゴジラ-1.0", "監督 山崎貴")]
    assert items[0]["key"] == "100001"


def test_change_then_no_change(env):
    settings = {"mail": CFG, "pref_codes": ["13"]}
    sat = datetime(2026, 10, 27, 9, 0)  # 火曜 (まとめの日ではない)
    r = notify.run(settings, env["dir"], WL, DWL, NOW, send_fn=env["send"], now=sat)
    assert r["sent"] and r["count"] == 2 and not r["error"]
    assert "新しく上映中" in env["sent"][0]["subject"] and "国宝" in env["sent"][0]["text"]
    r2 = notify.run(settings, env["dir"], WL, DWL, NOW, send_fn=env["send"], now=sat)
    assert not r2["sent"] and r2["why"] == "変化なし" and len(env["sent"]) == 1
    # ネット無し (取得が空) では忘れない
    r3 = notify.run(settings, env["dir"], WL, DWL, {}, send_fn=env["send"], now=sat)
    assert not r3["sent"]
    assert set(notify.load_json(env["dir"] / "notify_state.json")["sent"]) == {"100001", "100002"}
    # 上映が終わって (一覧に無い)、また始まったら知らせる
    r3 = notify.run(settings, env["dir"], WL, DWL, {"別の映画": NOW["別の映画"]}, send_fn=env["send"], now=sat)
    assert not r3["sent"] and notify.load_json(env["dir"] / "notify_state.json")["sent"] == {}
    r4 = notify.run(settings, env["dir"], WL, DWL, NOW, send_fn=env["send"], now=sat)
    assert r4["sent"] and r4["count"] == 2
    log = notify.load_json(env["dir"] / "notify_log.json")
    assert len(log) == 5 and all("secret" not in str(e) for e in log) and all("本文" not in e for e in log)


def test_weekly_digest_once_a_day(env):
    settings = {"mail": {**CFG, "on_change": False}, "pref_codes": []}
    sat8 = datetime(2026, 10, 31, 8, 30)  # 土曜
    sat7 = datetime(2026, 10, 31, 7, 30)
    assert not notify.run(settings, env["dir"], WL, DWL, NOW, send_fn=env["send"], now=sat7)["sent"]
    r = notify.run(settings, env["dir"], WL, DWL, NOW, send_fn=env["send"], now=sat8)
    assert r["sent"] and "まとめ" in env["sent"][0]["subject"] and "上映中 (2 本)" in env["sent"][0]["text"]
    assert not notify.run(settings, env["dir"], WL, DWL, NOW, send_fn=env["send"], now=sat8)["sent"]  # 同じ日は 2 回送らない
    assert notify.load_json(env["dir"] / "notify_state.json")["last_digest"] == "2026-10-31"


def test_digest_now_includes_theaters(env):
    settings = {"mail": CFG, "pref_codes": ["13"]}
    theaters = lambda mid, prefs: {"東京": [{"name": f"館{i}", "url": ""} for i in range(7)]} if mid == "100001" else {}
    r = notify.run(settings, env["dir"], WL, DWL, NOW, kind="digest", send_fn=env["send"], theaters_fn=theaters, now=datetime(2026, 10, 27, 9))
    assert r["sent"] and r["count"] == 2
    t = env["sent"][0]["text"]
    assert "東京: 館0, 館1, 館2, 館3, 館4 他2館" in t and "<a href=\"https://eiga.com/movie/100001/\">" in env["sent"][0]["html"]


def test_bad_config_and_missing_password(env, monkeypatch):
    r = notify.run({"mail": {**CFG, "port": 25}}, env["dir"], WL, DWL, NOW, send_fn=env["send"])
    assert not r["sent"] and "25" in r["error"]
    monkeypatch.setattr(notify, "get_password", lambda user: None)
    r = notify.run({"mail": CFG}, env["dir"], WL, DWL, NOW, send_fn=env["send"])
    assert not r["sent"] and "資格情報" in r["error"]
    assert env["sent"] == []


def test_send_error_is_recorded_and_state_not_advanced(env):
    settings = {"mail": CFG}
    r = notify.run(settings, env["dir"], WL, DWL, NOW, send_fn=lambda *a: "認証に失敗 (user かパスワード)", now=datetime(2026, 10, 27, 9))
    assert not r["sent"] and "認証" in r["error"]
    assert notify.load_json(env["dir"] / "notify_state.json") == {}
    r = notify.run(settings, env["dir"], WL, DWL, NOW, send_fn=env["send"], now=datetime(2026, 10, 27, 9))
    assert r["sent"] and r["count"] == 2


def test_test_mail_does_not_touch_state(env):
    r = notify.run({"mail": CFG}, env["dir"], WL, DWL, NOW, kind="test", send_fn=env["send"])
    assert r["sent"] and "試し" in env["sent"][0]["subject"]
    assert not (env["dir"] / "notify_state.json").exists()


def test_cfg_of_drops_password():
    c = notify.cfg_of({"mail": {"password": "x", "to": "a@b"}})
    assert "password" not in c and c["port"] == 465 and c["weekly"]["weekday"] == 5


# ---- MV-6: 雛形・次の一手・記録・次のまとめ ----
def test_presets_have_tls_ports_and_outlook_is_unusable():
    for name, p in notify.PRESETS.items():
        if p["usable"] and p["host"]:
            assert p["port"] in (465, 587) and p["mode"] in ("ssl", "starttls") and p["steps"]
    assert not notify.PRESETS["Outlook / Hotmail"]["usable"] and "OAuth2" in notify.PRESETS["Outlook / Hotmail"]["note"]
    assert notify.PRESETS["Gmail"]["host"] == "smtp.gmail.com" and notify.PRESETS["Gmail"]["mode"] == "ssl"


def test_hint_maps_errors_to_next_step():
    assert "アプリ パスワード" in notify.hint("認証に失敗 (user かパスワード)")
    assert "465" in notify.hint("サーバーが接続を切りました (host / port)")
    assert "ネット" in notify.hint("eiga に繋がりません")
    assert "保存" in notify.hint("パスワードが資格情報にありません")
    assert notify.PRESETS_CHECKED in notify.hint("送信に失敗 (SMTPException)")
    assert notify.hint("") == ""


def test_next_digest_and_recent_log(tmp_path):
    from datetime import datetime
    cfg = {"weekly": {"enabled": True, "weekday": 0, "hour": 8}}   # 月 8 時
    wed = datetime(2026, 10, 28, 10, 0)                              # 水
    assert notify.next_digest(cfg, {}, wed) == datetime(2026, 11, 2, 8, 0)
    mon7 = datetime(2026, 11, 2, 7, 0)
    assert notify.next_digest(cfg, {}, mon7) == datetime(2026, 11, 2, 8, 0)
    mon9 = datetime(2026, 11, 2, 9, 0)
    assert notify.next_digest(cfg, {}, mon9) == datetime(2026, 11, 2, 8, 0)          # まだ送っていない → 今日
    assert notify.next_digest(cfg, {"last_digest": "2026-11-02"}, mon9) == datetime(2026, 11, 9, 8, 0)
    assert notify.next_digest({"weekly": {"enabled": False}}, {}, mon9) is None
    save_json(tmp_path / "notify_log.json", [{"at": "a", "kind": "skip", "why": "変化なし"}, {"at": "b", "kind": "change", "count": 2, "ok": True, "error": ""}, {"at": "c", "kind": "test", "count": 0, "ok": False, "error": "認証に失敗"}])
    got = notify.recent_log(tmp_path, 2)
    assert [e["at"] for e in got] == ["c", "b"]
    assert notify.log_line(got[0]).startswith("c  試し  ⚠ 認証") and "✅ 2 件" in notify.log_line(got[1])
    assert "(変化なし)" in notify.log_line({"at": "a", "kind": "skip", "why": "変化なし"})
    assert notify.recent_log(tmp_path / "none") == []


# ---- MV-10: まとめの頭 ----
def test_digest_has_picks_and_glow_on_top():
    pk = [{"movie": {"title": "怪物", "director": "是枝裕和", "movie_id": "9", "url": "https://eiga.com/movie/9/"}, "reason": "なぜ: 監督 是枝裕和 +1.2"}]
    th = lambda mid, prefs: {"東京": [{"name": "シネマA", "url": ""}]}
    subject, text, html = notify.build_message([], [], ["13", "23"], th, "digest", pk, "今週: 監督 是枝裕和 に惹かれています (観たい 2)")
    assert text.startswith("今週: 監督 是枝裕和") and "■ おすすめ" in text and "📍 シネマA" in text and "https://eiga.com/movie/9/" in text
    assert "<h3>おすすめ</h3>" in html and "怪物" in html
    _, text2, _ = notify.build_message([], [], ["13"], None, "change", pk, "x")
    assert "おすすめ" not in text2


def test_run_digest_calls_picks_fn_and_survives_failure(tmp_path):
    settings = {"mail": {"to": "a@b.c", "host": "h", "port": 465, "user": "u", "mode": "ssl", "on_change": True, "weekly": {"enabled": True, "weekday": 0, "hour": 8}}}
    sent = {}
    def send_fn(cfg, pw, subject, text, html):
        sent["text"] = text
        return ""
    import moviecheck.notify as n
    n.get_password = lambda user: "pw"
    boom = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    r = notify.run(settings, tmp_path, [], [], {"怪物": {"title": "怪物"}}, kind="digest", send_fn=send_fn, picks_fn=boom)
    assert r["sent"] and "おすすめ" not in sent["text"]
    r = notify.run(settings, tmp_path, [], [], {"怪物": {"title": "怪物"}}, kind="digest", send_fn=send_fn,
                   picks_fn=lambda: ([{"movie": {"title": "怪物"}, "reason": "人気 1 位"}], "今週: 1 本に反応"))
    assert r["sent"] and sent["text"].startswith("今週: 1 本に反応") and "■ おすすめ" in sent["text"]


# ---- MV-19: 映画館 WL の作品は上映中として並び、登録時に『既知』にできる ----
def test_theater_wl_items_and_mark_known(tmp_path):
    thw = [{"name": "ユーロスペース", "movies": [{"title": "A", "movie_id": "1", "url": "u1"}, {"title": "B", "movie_id": "2"}]}]
    items = notify.showing_items([], [], {"A": {"title": "A", "movie_id": "1", "director": "D"}}, thw)
    assert [(i["key"], i["source"], i["director"]) for i in items] == [("th:ユーロスペース:1", "映画館 ユーロスペース", "D"), ("th:ユーロスペース:2", "映画館 ユーロスペース", "")]
    notify.mark_known(tmp_path, items)
    state = notify.load_json(tmp_path / "notify_state.json")
    assert set(state["sent"]) == {"th:ユーロスペース:1", "th:ユーロスペース:2"}
    assert notify.changes(items, state) == []
    thw[0]["movies"].append({"title": "C", "movie_id": "3"})
    items2 = notify.showing_items([], [], {}, thw)
    assert [i["key"] for i in notify.changes(items2, state)] == ["th:ユーロスペース:3"]
