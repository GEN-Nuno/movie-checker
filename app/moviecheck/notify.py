"""メール通知 (MV-2): 変化の検出 → 文面 → SMTP (TLS) → 記録。
パスワードは Windows の資格情報 (keyring) に置き、json にも源にもログにも書かない。"""
from __future__ import annotations

import html as htmlmod
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

from .match import find_match
from .store import load_json, save_json

SERVICE = "moviecheck"          # 資格情報の名前
LOG_MAX = 200                   # notify_log.json に残す件数
THEATERS_PER_PREF = 5
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]

DEFAULT_CFG = {
    "to": "", "host": "", "port": 465, "user": "", "mode": "ssl",   # ssl (465) | starttls (587)
    "on_change": True, "weekly": {"enabled": False, "weekday": 5, "hour": 8},
}


# ---- 資格情報 ----
def set_password(user: str, password: str) -> str:
    """keyring に置く。失敗なら訳を返す (空なら成功)。"""
    try:
        import keyring
        keyring.set_password(SERVICE, user, password)
        return ""
    except Exception as e:  # keyring が無い・資格情報マネージャーが使えない
        return f"資格情報に保存できません ({type(e).__name__})"


def get_password(user: str) -> str | None:
    try:
        import keyring
        return keyring.get_password(SERVICE, user)
    except Exception:
        return None


def has_password(user: str) -> bool:
    return bool(user) and get_password(user) is not None


def cfg_of(settings: dict) -> dict:
    """settings.json の mail を既定で埋めて返す (パスワードは入らない)。"""
    c = dict(DEFAULT_CFG)
    c.update(settings.get("mail") or {})
    w = dict(DEFAULT_CFG["weekly"])
    w.update(c.get("weekly") or {})
    c["weekly"] = w
    c.pop("password", None)   # 万一入っていても使わない
    return c


def cfg_problem(cfg: dict) -> str:
    if not cfg.get("to") or "@" not in cfg["to"]:
        return "宛先が空です"
    if not cfg.get("host"):
        return "SMTP の host が空です"
    try:
        port = int(cfg.get("port") or 0)
    except (TypeError, ValueError):
        return "port は数字です"
    if port == 25:
        return "25 番 (平文) は使えません。465 (SSL) か 587 (STARTTLS)"
    if cfg.get("mode") not in ("ssl", "starttls"):
        return "mode は ssl か starttls"
    if not cfg.get("user"):
        return "SMTP の user が空です"
    return ""


# ---- 今 🟢 の物 ----
def showing_items(watchlist: list, dir_watchlist: list, now_showing: dict) -> list[dict]:
    """映画 WL と監督 WL のうち、今上映中の物。{key, title, director, movie_id, url, source}。"""
    out, seen = [], set()
    for item in watchlist or []:
        mt = find_match(item.get("title", ""), now_showing)
        if not mt:
            continue
        info = now_showing.get(mt, {})
        key = info.get("movie_id") or mt
        if key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "title": mt, "director": item.get("director") or info.get("director", ""),
                    "movie_id": info.get("movie_id", ""), "url": info.get("url", ""), "source": "観たい映画"})
    by_id = {info.get("movie_id"): (t, info) for t, info in now_showing.items() if info.get("movie_id")}
    for dw in dir_watchlist or []:
        name = dw.get("name", "")
        for m in dw.get("movies", []):
            title = m.get("title", "")
            mid = m.get("movie_id", "")
            info = now_showing.get(title)
            if info is None and mid in by_id:
                title, info = by_id[mid]
            if info is None:
                continue
            key = info.get("movie_id") or title
            if key in seen:
                continue
            seen.add(key)
            out.append({"key": key, "title": title, "director": name, "movie_id": info.get("movie_id", ""),
                        "url": info.get("url", ""), "source": f"監督 {name}"})
    return out


def changes(items: list[dict], state: dict) -> list[dict]:
    """前回送った物 (state.sent) に無い物。"""
    sent = state.get("sent") or {}
    return [it for it in items if it["key"] not in sent]


def digest_due(cfg: dict, state: dict, now: datetime) -> bool:
    w = cfg.get("weekly") or {}
    if not w.get("enabled"):
        return False
    if now.weekday() != int(w.get("weekday", 5)) or now.hour < int(w.get("hour", 8)):
        return False
    return state.get("last_digest") != now.strftime("%Y-%m-%d")


# ---- 文面 ----
def build_message(new_items: list[dict], all_items: list[dict], pref_codes: list[str],
                  theaters_fn: Callable[[str, list[str]], dict] | None, kind: str) -> tuple[str, str, str]:
    """(件名, テキスト, HTML)。kind = change | digest | test。"""
    today = datetime.now().strftime("%Y-%m-%d")
    if kind == "test":
        subject = "[映画チェッカー] 試しの送信"
        text = f"映画チェッカーからの試しのメールです ({today})。この文が届けば設定は合っています。"
        return subject, text, f"<p>{htmlmod.escape(text)}</p>"
    if kind == "change":
        subject = f"[映画チェッカー] 新しく上映中: {', '.join(it['title'] for it in new_items[:3])}" + ("…" if len(new_items) > 3 else "")
    else:
        subject = f"[映画チェッカー] 今週のまとめ ({today}) — 上映中 {len(all_items)} 本"
    tl, hl = [], []
    if new_items:
        tl.append("■ 新しく上映になった物")
        hl.append("<h3>新しく上映になった物</h3><ul>")
        for it in new_items:
            _item_lines(it, pref_codes, theaters_fn, tl, hl)
        hl.append("</ul>")
    if kind == "digest":
        rest = [it for it in all_items if it not in new_items]
        tl.append("")
        tl.append(f"■ 上映中 ({len(all_items)} 本)")
        hl.append(f"<h3>上映中 ({len(all_items)} 本)</h3><ul>")
        for it in rest:
            _item_lines(it, pref_codes, None, tl, hl)
        hl.append("</ul>")
    tl.append("")
    tl.append("— 映画チェッカー (このメールに返信しても届きません)")
    text = "\n".join(tl)
    html = "<html><body style='font-family:sans-serif'>" + "\n".join(hl) + "<p style='color:#888'>— 映画チェッカー</p></body></html>"
    return subject, text, html


def _item_lines(it: dict, pref_codes, theaters_fn, tl, hl):
    d = f"（{it['director']}）" if it.get("director") else ""
    url = it.get("url") or ""
    tl.append(f"・{it['title']}{d}  [{it['source']}]")
    if url:
        tl.append(f"   {url}")
    h = f"<li><b>{htmlmod.escape(it['title'])}</b>{htmlmod.escape(d)} <small>[{htmlmod.escape(it['source'])}]</small>"
    if url:
        h += f' <a href="{htmlmod.escape(url)}">eiga.com</a>'
    if theaters_fn and it.get("movie_id") and pref_codes:
        try:
            th = theaters_fn(it["movie_id"], list(pref_codes))
        except Exception:
            th = {}
        for pref, lst in th.items():
            names = [t["name"] for t in lst[:THEATERS_PER_PREF]]
            more = f" 他{len(lst) - THEATERS_PER_PREF}館" if len(lst) > THEATERS_PER_PREF else ""
            tl.append(f"   {pref}: {', '.join(names)}{more}")
            h += f"<br><small>{htmlmod.escape(pref)}: {htmlmod.escape(', '.join(names))}{htmlmod.escape(more)}</small>"
    hl.append(h + "</li>")


# ---- 送る ----
def send(cfg: dict, password: str, subject: str, text: str, html: str) -> str:
    """SMTP で送る。失敗なら訳 (空なら成功)。証明書は必ず確かめる (verify を外す設定は無い)。"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["user"] if "@" in cfg["user"] else cfg["to"]
    msg["To"] = cfg["to"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    port = int(cfg["port"])
    try:
        if cfg["mode"] == "ssl":
            with smtplib.SMTP_SSL(cfg["host"], port, context=ctx, timeout=20) as s:
                s.login(cfg["user"], password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], port, timeout=20) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.ehlo()
                s.login(cfg["user"], password)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return "認証に失敗 (user かパスワード。Gmail はアプリ パスワード)"
    except ssl.SSLError as e:
        return f"TLS で失敗 ({e.reason or 'certificate'})"
    except smtplib.SMTPServerDisconnected:
        return "サーバーが接続を切りました (host / port / SSL か STARTTLS の組を確認)"
    except (smtplib.SMTPException, OSError) as e:
        return f"送信に失敗 ({type(e).__name__}: {str(e)[:60]})"
    return ""


# ---- まとめて 1 回 ----
def run(settings: dict, data_dir: Path, watchlist: list, dir_watchlist: list, now_showing: dict,
        kind: str = "auto", theaters_fn=None, send_fn=None, now: datetime | None = None) -> dict:
    """kind: auto (変化があれば / まとめの日なら) | digest (今すぐまとめ) | test (試し)。
    戻り {sent: bool, count: int, error: str, why: str}。記録は notify_log.json、送った物は notify_state.json。"""
    now = now or datetime.now()
    cfg = cfg_of(settings)
    send_fn = send_fn or send
    state_p = data_dir / "notify_state.json"
    log_p = data_dir / "notify_log.json"
    state = load_json(state_p) or {}
    if not isinstance(state, dict):
        state = {}
    log = load_json(log_p)
    if not isinstance(log, list):
        log = []

    def record(kind_, count, error, why=""):
        log.append({"at": now.strftime("%Y-%m-%d %H:%M"), "kind": kind_, "count": count, "ok": not error, "error": error, "why": why})
        save_json(log_p, log[-LOG_MAX:])
        return {"sent": not error and kind_ != "skip", "count": count, "error": error, "why": why}

    prob = cfg_problem(cfg)
    if prob:
        return record("skip", 0, "", prob) | {"error": prob}
    items = showing_items(watchlist, dir_watchlist, now_showing)
    # 上映が終わった物は忘れる (また上映されたら知らせる)。取得が空の時は触らない (ネット無しで全部忘れない)
    if now_showing:
        keys = {it["key"] for it in items}
        pruned = {k: v for k, v in (state.get("sent") or {}).items() if k in keys}
        if pruned != (state.get("sent") or {}):
            state["sent"] = pruned
            save_json(state_p, state)
    new = changes(items, state)
    if kind == "test":
        real_kind, chosen = "test", []
    elif kind == "digest":
        real_kind, chosen = "digest", new
    else:
        if new and cfg.get("on_change"):
            real_kind, chosen = "change", new
        elif digest_due(cfg, state, now):
            real_kind, chosen = "digest", new
        else:
            return record("skip", 0, "", "変化なし")
    if real_kind == "change" and not chosen:
        return record("skip", 0, "", "変化なし")
    password = get_password(cfg["user"])
    if password is None:
        return record(real_kind, 0, "パスワードが資格情報にありません (設定で保存)")
    subject, text, html = build_message(chosen, items, settings.get("pref_codes", ["13"]), theaters_fn, real_kind)
    err = send_fn(cfg, password, subject, text, html)
    if err:
        return record(real_kind, len(chosen), err)
    if real_kind != "test":
        sent = dict(state.get("sent") or {})
        for it in items:          # まとめでも『今 🟢 の物』は送った事にする
            sent[it["key"]] = now.strftime("%Y-%m-%d")
        state["sent"] = sent
        if real_kind == "digest":
            state["last_digest"] = now.strftime("%Y-%m-%d")
        save_json(state_p, state)
    return record(real_kind, len(chosen) if real_kind == "change" else len(items), "")
