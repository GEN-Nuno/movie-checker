"""
映画チェッカー (movie_app)
eiga.com から上映中・公開予定の映画を取り、ウォッチリスト (映画・監督・映画館) と照らし合わせるデスクトップ アプリ。
画面はこの file、取得・照合・通知・好み・おすすめは moviecheck/ (版は moviecheck/version.py)。

機能:
  - 起動時に上映情報を取る (キャッシュが 6 時間以内なら再利用)。裏で 6 時間ごとに取り直す
  - ウォッチリストの映画が上映中か、まだなら公開日がいつか
  - 上映中の一覧のダブルクリック → 地域の映画館の窓
  - おすすめの札 (気分・好みの学習)、映画館のウォッチリスト、メールの通知、dark / light

(元は 映画チェッカー.exe (PyInstaller、Python 3.10) の bytecode から 2026-10-30 に書き起こした物。以後の改修は本棚 movie.json の MV-1〜)
"""
import os
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from macui import Button  # Mac は色が効く平らなボタン、Windows は tk.Button

from moviecheck import fetch, notify, paths, picks, store, taste
try:  # ポスター (MV-9)。無ければ灰色の枠
    from PIL import Image, ImageTk
except Exception:  # noqa: BLE001
    Image = ImageTk = None
from moviecheck.version import __version__
from mail_dialog import MailSettingsDialog
from moviecheck.fetch import (  # noqa: F401 — 画面が使う名前 (置き場だけ変えた)
    EIGA_BASE, PREFECTURES,
    fetch_now_showing, fetch_ranking, fetch_theaters_multi, search_eiga,
    fetch_movie_director, fetch_person_id, fetch_directed_movies, search_persons,
)
from moviecheck.match import normalize, find_match, suggest_score, ranked_suggestions  # noqa: F401
from moviecheck.store import load_json, save_json

# ---- 置き場 (exe なら exe の隣、源なら .py の隣) ----
_FROZEN = bool(getattr(sys, "frozen", False))
if _FROZEN:
    EXE_DIR = Path(sys.executable).parent
    _BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", EXE_DIR))
else:
    EXE_DIR = Path(__file__).parent
    _BUNDLE_DIR = EXE_DIR
# データの置き場 (MV-4): exe の隣に json があれば隣、無ければ %APPDATA%\映画チェッカー。源からは .py の隣
APP_DIR = paths.data_dir(EXE_DIR, _FROZEN)


def open_folder(path: Path) -> None:
    """置き場を OS のファイラで開く。Windows は startfile、Mac は open。"""
    if hasattr(os, "startfile"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        import subprocess
        subprocess.Popen(["open", str(path)])


_MAC = sys.platform == "darwin"


def ui_font(size: int, semibold: bool = False):
    """字体の指定。Windows は今までどおり Segoe UI。Mac には Segoe UI が無く既定の字体に落ちて太字も消えるので、
    システム字体に寄せ、Windows (96dpi) と見た目の大きさを揃えるため 3pt 足す。"""
    if _MAC:
        return (".AppleSystemUIFont", size + 3, "bold") if semibold else (".AppleSystemUIFont", size + 3)
    return ("Segoe UI Semibold", size) if semibold else ("Segoe UI", size)


def _tk_font(size: int, semibold: bool = False) -> "tkfont.Font":
    spec = ui_font(size, semibold)
    return tkfont.Font(family=spec[0], size=spec[1], weight="bold" if len(spec) > 2 else "normal")


def wheel_units(e) -> int:
    """ホイール 1 回分を xview_scroll / yview_scroll の units に。Windows の delta は ±120 刻み、Mac は ±1〜 (Tk が既に刻み数) なので、
    // 120 すると Mac では正の向きが 0 になり片方向しか動かない。"""
    return -e.delta if _MAC else -1 * (e.delta // 120)

ICON_FILE = _BUNDLE_DIR / "movie_icon.ico"
WATCHLIST_FILE = APP_DIR / "watchlist.json"
DIR_WL_FILE = APP_DIR / "director_watchlist.json"
SETTINGS_FILE = APP_DIR / "settings.json"
CACHE_FILE = APP_DIR / "movie_cache.json"
TASTE_FILE = APP_DIR / "taste.json"   # 好みの学習 (MV-8)
THEATER_WL_FILE = APP_DIR / "theater_watchlist.json"   # 映画館 WL (MV-19)
POSTERS_DIR = APP_DIR / "posters"       # 札のポスター 3 枚 (MV-9)

# eiga.com の URL・都道府県は moviecheck.fetch に (MV-1)
REGION_GROUPS = [
    ("北海道・東北", ["1", "2", "3", "4", "5", "6", "7"]),
    ("関東", ["13", "14", "11", "12", "8", "9", "10"]),
    ("中部", ["15", "16", "17", "18", "19", "20", "21", "22", "23", "24"]),
    ("近畿", ["25", "26", "27", "28", "29", "30"]),
    ("中国・四国", ["31", "32", "33", "34", "35", "36", "37", "38", "39"]),
    ("九州・沖縄", ["40", "41", "42", "43", "44", "45", "46", "47"]),
]

# ---- 色 ----
# 色の役割 (MV-18 → MV-20): 地と段、札と入力、押し物、線、字 3 段、青は操作だけ、緑は上映中の印、赤は削除と失敗。
# dark は apple.com の暗い段の型 (#000 の地に #1d1d1f の札、青は #2997ff)。light は MV-18 の値。settings.theme で選ぶ (既定 dark)
THEMES = {
    "dark": {
        "hdr_bg": "#000000", "hdr_fg": "#f5f5f7", "hdr_fg2": "#a1a1a6", "hdr_btn": "#1d1d1f",
        "bar_bg": "#000000", "bar_fg": "#86868b",
        "bg": "#111113", "bg2": "#111113", "bg3": "#2c2c2e",
        "surface": "#1d1d1f", "overlay": "#3a3a3c",
        "text": "#f5f5f7", "text2": "#a1a1a6", "text3": "#86868b",
        "red": "#ff453a", "green": "#30d158", "yellow": "#ffd60a",
        "blue": "#2997ff", "mauve": "#bf5af2", "pink": "#ff375f",
        "teal": "#64d2ff", "peach": "#ff9f0a",
        "accent": "#2997ff", "accent_hover": "#4aa8ff", "on_accent": "#ffffff",
        "input_bg": "#1d1d1f", "border": "#3a3a3c",
        "card": "#1d1d1f", "card_alt": "#232326", "separator": "#3a3a3c", "sel_bg": "#2997ff", "sel_fg": "#ffffff",
    },
    "light": {
        "hdr_bg": "#1d1d1f", "hdr_fg": "#f5f5f7", "hdr_fg2": "#a1a1a6", "hdr_btn": "#2c2c2e",
        "bar_bg": "#2c2c2e", "bar_fg": "#a1a1a6",
        "bg": "#ffffff", "bg2": "#ffffff", "bg3": "#e8e8ed",
        "surface": "#ffffff", "overlay": "#d2d2d7",
        "text": "#1d1d1f", "text2": "#6e6e73", "text3": "#86868b",
        "red": "#e30000", "green": "#1d9a4b", "yellow": "#ffcc00",
        "blue": "#0071e3", "mauve": "#af52de", "pink": "#ff2d55",
        "teal": "#5ac8fa", "peach": "#ff9500",
        "accent": "#0071e3", "accent_hover": "#0077ed", "on_accent": "#ffffff",
        "input_bg": "#ffffff", "border": "#d2d2d7",
        "card": "#f5f5f7", "card_alt": "#fbfbfd", "separator": "#d2d2d7", "sel_bg": "#e3f0ff", "sel_fg": "#0071e3",
    },
}


def _theme_name() -> str:
    st = load_json(SETTINGS_FILE)
    t = st.get("theme") if isinstance(st, dict) else None
    return t if t in THEMES else "dark"


C = dict(THEMES[_theme_name()])


def _merge_ranking(ms: list[dict], rk: list[dict]) -> dict:
    """上映中 (人気順、image / synopsis / cast つき) にランキングの題名を足す。同じ題名はランキングで上書きしない
    (MV-20 で見つけた: ランキングのページには image と synopsis が無く、上書きで人気 1〜3 位の写真と気分が消えていた)。"""
    d = {m["title"]: m for m in ms}
    for m in rk:
        cur = d.get(m["title"])
        if cur is None:
            d[m["title"]] = m
        else:
            for k, v in m.items():
                if v and not cur.get(k):
                    cur[k] = v
    return d


# =====================================================================
# 部品: オートコンプリートの入力欄
# =====================================================================
class AutocompleteEntry(tk.Entry):
    """指数スコアリングで候補を推薦するオートコンプリートEntry

    Args:
        candidates_func: list[str] を返す callable
        on_selected: 候補選択時のコールバック (選択値) -> None
        alpha: 指数減衰の鋭さ（大きいほど厳選）
        threshold: 表示する最低スコア
    """

    def __init__(self, master, candidates_func, on_selected=None, async_search_func=None,
                 info_func=None, alpha=3.0, threshold=0.15, **kwargs):
        super().__init__(master, **kwargs)
        self._candidates_func = candidates_func
        self._on_selected_cb = on_selected
        self._async_search_func = async_search_func
        self._info_func = info_func
        self._alpha = alpha
        self._threshold = threshold
        self._lb = None
        self._lb_win = None
        self._async_timer = None
        self._async_lock = threading.Lock()
        self._selected_value = None
        self.bind("<KeyRelease>", self._on_key)
        self.bind("<FocusOut>", self._schedule_hide)
        self.bind("<Escape>", self._hide_lb)

    @property
    def is_from_suggestion(self) -> bool:
        val = self.get().strip()
        return bool(val and self._selected_value and val == self._selected_value)

    def _on_key(self, event):
        if event.keysym in ("Escape", "Up", "Tab"):
            return
        if event.keysym == "Down" and self._lb_win and self._lb:
            try:
                if self._lb_win.winfo_ismapped() and self._lb.size() > 0:
                    self._lb.focus_set()
                    self._lb.selection_set(0)
            except tk.TclError:
                pass
            return
        current = self.get().strip()
        if self._selected_value and current != self._selected_value:
            self._selected_value = None
        self.after(1, self._evaluate_input)

    def _evaluate_input(self):
        val = self.get().strip()
        if not val or val == getattr(self, "_ph", ""):
            self._hide_lb()
            return
        candidates = self._candidates_func()
        results = ranked_suggestions(val, candidates, threshold=self._threshold, alpha=self._alpha)
        if results:
            self._show_lb(results)
        else:
            self._hide_lb()
        if self._async_search_func and len(val) >= 2:
            self._trigger_async_search(val)

    def _show_lb(self, scored_items: list[tuple[str, float]]):
        if self._lb_win and self._lb:
            try:
                self._lb.delete(0, "end")
            except tk.TclError:
                self._lb_win = None
                self._lb = None
        if not self._lb_win:
            self._lb_win = tk.Toplevel(self)
            self._lb_win.wm_overrideredirect(True)
            self._lb_win.configure(bg=C["border"])
            self._lb = tk.Listbox(
                self._lb_win, bg=C["card"], fg=C["text"],
                selectbackground=C["accent"], selectforeground="white",
                font=ui_font(11), relief="flat", borderwidth=1,
                activestyle="none",
            )
            self._lb.pack(fill="both", expand=True)
            self._lb.bind("<ButtonRelease-1>", self._do_select)
            self._lb.bind("<Return>", self._do_select)
        for name, score in scored_items:
            info = self._info_func(name) if self._info_func else ""
            if info:
                self._lb.insert("end", f"{name}  ─ {info}")
            else:
                self._lb.insert("end", name)
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        w = max(self.winfo_width(), 400)
        h = min(len(scored_items), 8) * 28 + 4
        self._lb_win.geometry(f"{w}x{h}+{x}+{y}")
        self._lb_win.lift()
        self._lb_win.deiconify()
        self._item_names = [name for name, _ in scored_items]

    def _schedule_hide(self, event=None):
        self.after(150, self._hide_lb)

    def _hide_lb(self, event=None):
        if self._lb_win:
            try:
                self._lb_win.withdraw()
            except tk.TclError:
                self._lb_win = None

    def _do_select(self, event=None):
        if self._lb and self._lb.curselection():
            idx = self._lb.curselection()[0]
            if hasattr(self, "_item_names"):
                val = self._item_names[idx]
            else:
                val = self._lb.get(idx).split("  ")[0]
            self.delete(0, "end")
            self.insert(0, val)
            self._selected_value = val
            self._hide_lb()
            self.focus_set()
            if self._on_selected_cb:
                self._on_selected_cb(val)

    def destroy(self):
        if self._lb_win:
            try:
                self._lb_win.destroy()
            except Exception:
                pass
        super().destroy()

    def _trigger_async_search(self, query: str):
        if self._async_timer:
            self.after_cancel(self._async_timer)
        self._async_timer = self.after(500, lambda: self._run_async_search(query))

    def _run_async_search(self, query: str):
        def work():
            try:
                results = self._async_search_func(query)
            except Exception:
                results = []
            if not results:
                return
            self.after(0, lambda: self._merge_async_results(query, results))

        threading.Thread(target=work, daemon=True).start()

    def _merge_async_results(self, original_query: str, async_candidates: list[str]):
        current = self.get().strip()
        if not current or current == getattr(self, "_ph", ""):
            return
        local = self._candidates_func()
        results = ranked_suggestions(current, local, threshold=self._threshold, alpha=self._alpha)
        seen = {name for name, _ in results}
        for ac in async_candidates:
            if ac not in seen:
                s = suggest_score(current, ac, alpha=self._alpha)
                if s < self._threshold:
                    s = self._threshold
                results.append((ac, s))
                seen.add(ac)
        results.sort(key=lambda x: -x[1])
        results = results[:10]
        if results:
            self._show_lb(results)
        else:
            self._hide_lb()


# =====================================================================
# 部品: 地域 (都道府県) の選択ダイアログ
# =====================================================================
class RegionSelector(tk.Toplevel):
    """チェックボタンで複数都道府県を選択するダイアログ"""

    def __init__(self, master, current_codes: list[str], on_save, F=None):
        super().__init__(master)
        F = F or {}
        f_h, f_body, f_small, f_btn = (F.get("h2") or ui_font(12, semibold=True)), (F.get("normal") or ui_font(10)), (F.get("small") or ui_font(9)), (F.get("btn") or ui_font(10))
        self.title("地域")
        self.geometry("520x520")
        self.configure(bg=C["bg"])
        self.transient(master)
        self.grab_set()
        self._on_save = on_save
        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())
        self._vars = {}

        hdr = tk.Frame(self, bg=C["bg"], pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="劇場を探す地域", font=f_h,
                 fg=C["text"], bg=C["bg"]).pack(padx=20, anchor="w")
        tk.Label(hdr, text="複数選択可能。劇場ポップアップで表示される地域です。",
                 font=f_small, fg=C["text2"], bg=C["bg"]).pack(padx=24, anchor="w")

        sep = tk.Frame(self, bg=C["separator"], height=1)
        sep.pack(fill="x", padx=20)

        canvas = tk.Canvas(self, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        sf = tk.Frame(canvas, bg=C["bg"])
        sf.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=sf, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True, padx=12, pady=8)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(wheel_units(e), "units"))

        if _MAC:
            st = ttk.Style()
            st.configure("Region.TCheckbutton", font=f_body, background=C["bg"], foreground=C["text"], indicatorcolor=C["input_bg"])
            st.map("Region.TCheckbutton", background=[("active", C["bg"])], foreground=[("active", C["accent"])], indicatorcolor=[("selected", C["accent"])])
        for region_name, codes in REGION_GROUPS:
            tk.Label(sf, text=region_name, font=f_body,
                     fg=C["text"], bg=C["bg"]).pack(anchor="w", padx=8, pady=(14, 4))
            row = tk.Frame(sf, bg=C["bg"])
            row.pack(fill="x", padx=12)
            for i, code in enumerate(codes):
                var = tk.BooleanVar(value=code in current_codes)
                self._vars[code] = var
                if _MAC:  # Aqua は tk.Checkbutton の bg を無視するので、Tk 自身が描く ttk (clam) に。色は下の Region.TCheckbutton
                    cb = ttk.Checkbutton(row, text=PREFECTURES[code], variable=var, style="Region.TCheckbutton")
                else:
                    cb = tk.Checkbutton(
                        row, text=PREFECTURES[code], variable=var,
                        bg=C["bg"], fg=C["text"], selectcolor=C["bg2"],
                        activebackground=C["bg"], activeforeground=C["accent"],
                        font=f_body,
                    )
                cb.grid(row=i // 4, column=i % 4, sticky="w", padx=4, pady=2)

        bf = tk.Frame(self, bg=C["bg"])
        bf.pack(fill="x", padx=20, pady=12)
        Button(bf, text="保存", font=f_btn, bg=C["accent"], fg="white",
                  relief="flat", cursor="hand2", bd=0, padx=20, pady=6,
                  command=self._save).pack(side="right", padx=(8, 0))
        Button(bf, text="キャンセル", font=f_btn, bg=C["bg3"], fg=C["text2"],
                  relief="flat", cursor="hand2", bd=0, padx=16, pady=6,
                  command=self.destroy).pack(side="right")
        Button(bf, text="全解除", font=f_small, bg=C["bg3"], fg=C["text2"],
                  relief="flat", cursor="hand2", bd=0, padx=10, pady=4,
                  command=self._clear_all).pack(side="left")
        Button(bf, text="関東", font=f_small, bg=C["bg3"], fg=C["text2"],
                  relief="flat", cursor="hand2", bd=0, padx=10, pady=4,
                  command=self._select_kanto).pack(side="left", padx=(8, 0))
        self._canvas = canvas

    def _save(self):
        codes = [c for c, v in self._vars.items() if v.get()]
        if not codes:
            messagebox.showinfo("確認", "少なくとも1つの地域を選択してください。", parent=self)
            return
        self._on_save(codes)
        try:
            self._canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        self.destroy()

    def _clear_all(self):
        for v in self._vars.values():
            v.set(False)

    def _select_kanto(self):
        kanto = {"8", "9", "10", "11", "12", "13", "14"}
        for code, v in self._vars.items():
            v.set(code in kanto)


# =====================================================================
# 画面の本体
# =====================================================================
class MovieApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"映画上映チェッカー {__version__}")
        self.root.geometry("1160x750")
        self.root.minsize(1000, 600)
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.configure(bg=C["bg"])
        if ICON_FILE.exists():
            try:
                self.root.iconbitmap(str(ICON_FILE))
            except tk.TclError:
                pass
            if sys.platform == "darwin" and Image is not None and ImageTk is not None:
                try:   # Mac は .ico を iconbitmap に渡せないので、PIL で読んで iconphoto に
                    self._icon_img = ImageTk.PhotoImage(Image.open(ICON_FILE))
                    self.root.iconphoto(True, self._icon_img)
                except Exception:  # noqa: BLE001
                    pass
        self.watchlist = load_json(WATCHLIST_FILE)
        self.dir_watchlist = load_json(DIR_WL_FILE)
        self.theater_wl = load_json(THEATER_WL_FILE)          # [{name, url, pref, area, movies, updated, error}] (MV-19)
        if not isinstance(self.theater_wl, list):
            self.theater_wl = []
        self.now_showing = {}
        self._ns_extra = {}
        self._eiga_search_cache = {}
        self.settings = load_json(SETTINGS_FILE) or {"pref_codes": ["13"]}
        # 古い設定 (pref_code 1 つ) を pref_codes に移す
        if "pref_code" in self.settings and "pref_codes" not in self.settings:
            self.settings["pref_codes"] = [self.settings.pop("pref_code")]
            save_json(SETTINGS_FILE, self.settings)
        if "pref_codes" not in self.settings:
            self.settings["pref_codes"] = ["13"]
        self.is_fetching = False
        self._timer = None
        self._next_update = None          # 次の自動更新 (MV-7 の帯)
        self._last_fetch = {"ts": "", "src": "", "error": ""}
        self._fetch_page = None
        self.taste = taste.Model(TASTE_FILE)   # 好みの学習 (MV-8)
        self._ns_scores = {}
        self._ns_sort = "rank"            # rank | taste
        self._mood = "おまかせ"             # 今日はどんな気分? (MV-9)
        self._picks = []
        self._picks_seen = set()          # 『次の候補』で除く題名 (その場だけ、MV-20)
        self._coming = []                 # 公開予定 (MV-22、取得のスレッドで coming_cached)
        self._poster_imgs = {}
        self._pick_cards = []
        # 字体 (MV-13、HIG typography): 1 書体、4 段。細字なし。太字は見出し (h) と札の題名 (strong) だけ
        self.F = {
            "title": _tk_font(14, semibold=True),
            "h2": _tk_font(16, semibold=True),
            "strong": _tk_font(10, semibold=True),
            "normal": _tk_font(10),
            "small": _tk_font(9),
            "btn": _tk_font(10),
            "caption": _tk_font(9),
        }
        self._setup_styles()
        self._restore_window()
        self._build_ui()
        self._refresh_wl()
        self._show_store_notes()
        self.refresh_bar()
        self.root.after(100, self._maybe_import_old_data)
        self.root.after(300, self._startup_fetch)

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        for name in ("WL.Treeview", "NS.Treeview"):
            rh = 40 if name.startswith("WL") else 34
            s.configure(name, background=C["card"], foreground=C["text"], fieldbackground=C["card"],
                        font=ui_font(10), rowheight=rh, borderwidth=0)
            s.configure(f"{name}.Heading", background=C["bg3"], foreground=C["text2"],
                        font=ui_font(9), borderwidth=0, relief="flat", padding=(8, 6))
            s.map(name, background=[("selected", C["sel_bg"])], foreground=[("selected", C["sel_fg"])])
            s.map(f"{name}.Heading", background=[("active", C["bg3"])])
        s.configure("Prog.Horizontal.TProgressbar", troughcolor=C["bg3"], background=C["accent"],
                    borderwidth=0, thickness=4)
        s.configure("TCombobox", fieldbackground=C["input_bg"], background=C["bg3"], foreground=C["text"], arrowcolor=C["text2"],
                    bordercolor=C["border"], lightcolor=C["input_bg"], darkcolor=C["input_bg"], padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", C["input_bg"])], foreground=[("readonly", C["text"])],
              selectbackground=[("readonly", C["input_bg"])], selectforeground=[("readonly", C["text"])])
        self.root.option_add("*TCombobox*Listbox.background", C["card"])
        self.root.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", C["on_accent"])
        s.configure("Vertical.TScrollbar", troughcolor=C["bg"], background=C["bg3"], bordercolor=C["bg"], arrowcolor=C["text2"],
                    lightcolor=C["bg3"], darkcolor=C["bg3"])
        s.map("Vertical.TScrollbar", background=[("active", C["border"])])
        s.configure("TNotebook", background=C["bg"], borderwidth=0)
        s.configure("TNotebook.Tab", background=C["bg3"], foreground=C["text2"], padding=(10, 5), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", C["card"])], foreground=[("selected", C["text"])])
        for w in ("TCheckbutton", "TRadiobutton"):
            s.configure(w, background=C["bg"], foreground=C["text"], indicatorcolor=C["input_bg"])
            s.map(w, background=[("active", C["bg"])], indicatorcolor=[("selected", C["accent"])])

    def _build_ui(self):
        # ヘッダ (MV-18): 黒地に白い題名、操作は字だけの薄灰のボタン
        hdr = tk.Frame(self.root, bg=C["hdr_bg"], pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text="映画上映チェッカー", font=self.F["title"], fg=C["hdr_fg"], bg=C["hdr_bg"]).pack(side="left", padx=24)

        pf = tk.Frame(hdr, bg=C["hdr_bg"])
        pf.pack(side="right", padx=24)

        def hbtn(text, cmd):
            return Button(pf, text=text, font=self.F["btn"], bg=C["hdr_bg"], fg=C["hdr_fg2"], activebackground=C["hdr_btn"], activeforeground=C["hdr_fg"],
                             relief="flat", cursor="hand2", padx=10, pady=3, bd=0, command=cmd)

        tk.Label(pf, text="地域", font=self.F["small"], fg=C["hdr_fg2"], bg=C["hdr_bg"]).pack(side="left", padx=(0, 4))
        self.pref_label = tk.Label(pf, text="", font=self.F["small"], fg=C["hdr_fg"], bg=C["hdr_bg"])
        self.pref_label.pack(side="left", padx=(0, 8))
        hbtn("変更", self._open_region_selector).pack(side="left")
        hbtn("外観", self._toggle_theme).pack(side="left", padx=(8, 0))
        hbtn("メール", self._open_mail_settings).pack(side="left", padx=(8, 0))
        hbtn("好み", self._open_taste).pack(side="left", padx=(8, 0))
        hbtn("情報", self._open_about).pack(side="left", padx=(8, 0))
        self._update_pref_label()

        self.status_lbl = tk.Label(hdr, text="", font=self.F["caption"], fg=C["hdr_fg2"], bg=C["hdr_bg"])
        self.status_lbl.pack(side="right", padx=24)

        # 状況の帯 (MV-7): 濃い灰に薄灰の字
        self.bar_lbl = tk.Label(self.root, text="", font=self.F["caption"], fg=C["bar_fg"], bg=C["bar_bg"], anchor="w", padx=24, pady=4)
        self.bar_lbl.pack(fill="x")
        tk.Frame(self.root, bg=C["separator"], height=1).pack(fill="x")

        self.progress = ttk.Progressbar(self.root, style="Prog.Horizontal.TProgressbar", mode="determinate", maximum=fetch.TOTAL_PAGES)

        main = tk.PanedWindow(self.root, orient="horizontal", bg=C["bg"], sashwidth=1, sashrelief="flat")
        main.pack(fill="both", expand=True, padx=0, pady=0)
        self._main_pw = main

        # 読み順 (HIG layout): 重要な物を先頭側に。主 = おすすめ + 上映中 (左 680)、補助 = ウォッチリスト (右)
        main_pane = tk.Frame(main, bg=C["bg"])
        main.add(main_pane, width=680, minsize=440)
        self._build_right(main_pane)

        side_pane = tk.Frame(main, bg=C["bg"])
        main.add(side_pane, minsize=360)
        self._build_left(side_pane)

    def _build_left(self, p):
        self._left_panel = p
        self._left_mode = "movie"

        tab_bar = tk.Frame(p, bg=C["bg"])
        tab_bar.pack(fill="x", padx=10, pady=(10, 0))
        self._ltab_btn_movie = Button(
            tab_bar, text="映画", font=self.F["btn"], bg=C["accent"], fg="white", relief="flat", cursor="hand2",
            padx=14, pady=3, command=lambda: self._switch_left_tab("movie"))
        self._ltab_btn_movie.pack(side="left")
        self._ltab_btn_dir = Button(
            tab_bar, text="監督", font=self.F["btn"], bg=C["bg3"], fg=C["text2"], relief="flat", cursor="hand2",
            padx=14, pady=3, command=lambda: self._switch_left_tab("director"))
        self._ltab_btn_dir.pack(side="left", padx=(2, 0))
        self._ltab_btn_th = Button(
            tab_bar, text="映画館", font=self.F["btn"], bg=C["bg3"], fg=C["text2"], relief="flat", cursor="hand2",
            padx=14, pady=3, command=lambda: self._switch_left_tab("theater"))
        self._ltab_btn_th.pack(side="left", padx=(2, 0))
        self.wl_cnt = tk.Label(tab_bar, text="0件", font=self.F["small"], fg=C["text3"], bg=C["bg"])
        self.wl_cnt.pack(side="right")
        self.wl_hint = tk.Label(tab_bar, text="ダブルクリックで劇場検索", font=self.F["small"], fg=C["text3"], bg=C["bg"])
        self.wl_hint.pack(side="right", padx=(0, 8))

        tk.Label(tab_bar, text="ウォッチリスト", font=self.F["h2"], fg=C["text"], bg=C["bg"]).pack(side="left", padx=(0, 16), before=self._ltab_btn_movie)
        self._movie_wl_frame = tk.Frame(p, bg=C["bg"])
        self._build_movie_wl_view(self._movie_wl_frame)
        self._dir_wl_frame = tk.Frame(p, bg=C["bg"])
        self._build_dir_wl_view(self._dir_wl_frame)
        self._th_wl_frame = tk.Frame(p, bg=C["bg"])
        self._build_theater_wl_view(self._th_wl_frame)
        self._movie_wl_frame.pack(fill="both", expand=True)

    def _switch_left_tab(self, mode: str):
        if mode == self._left_mode:
            return
        self._left_mode = mode
        for fr in (self._movie_wl_frame, self._dir_wl_frame, self._th_wl_frame):
            fr.pack_forget()
        for b, m in ((self._ltab_btn_movie, "movie"), (self._ltab_btn_dir, "director"), (self._ltab_btn_th, "theater")):
            on = m == mode
            b.config(bg=C["accent"] if on else C["bg3"], fg="white" if on else C["text2"])
        if mode == "movie":
            self._movie_wl_frame.pack(fill="both", expand=True)
            self.wl_cnt.config(text=f"{len(self.watchlist)}件")
            self.wl_hint.config(text="ダブルクリックで劇場検索")
        elif mode == "director":
            self._dir_wl_frame.pack(fill="both", expand=True)
            self.wl_cnt.config(text=f"{len(self.dir_watchlist)}人")
            self.wl_hint.config(text="ダブルクリックで劇場/作品ページ")
            self._refresh_dir_wl()
        else:
            self._th_wl_frame.pack(fill="both", expand=True)
            self.wl_cnt.config(text=f"{len(self.theater_wl)}館")
            self.wl_hint.config(text="")
            self._refresh_theater_wl()
            if not self._th_choices:
                self._load_theater_choices()

    # ---- 映画館のウォッチリスト (MV-19) ----
    def _build_theater_wl_view(self, p):
        tk.Label(p, text="館を登録すると、その館の今の上映作品が並び、新しく掛かった作品はメールの新着に入ります", font=self.F["caption"],
                 fg=C["text3"], bg=C["bg"], wraplength=420, justify="left").pack(fill="x", padx=16, pady=(8, 0))
        inp = tk.Frame(p, bg=C["card"], padx=14, pady=10)
        inp.pack(fill="x", padx=16, pady=(6, 0))
        row1 = tk.Frame(inp, bg=C["card"])
        row1.pack(fill="x", pady=(0, 6))
        tk.Label(row1, text="都道府県", font=self.F["small"], fg=C["text2"], bg=C["card"], width=7, anchor="w").pack(side="left")
        self._th_pref_var = tk.StringVar(value=PREFECTURES.get((self.pref_codes or ["13"])[0], "東京"))
        self._th_pref_combo = ttk.Combobox(row1, textvariable=self._th_pref_var, state="readonly", font=ui_font(10), width=10,
                                           values=[PREFECTURES[c] for c in sorted(PREFECTURES, key=int)])
        self._th_pref_combo.pack(side="left", padx=(6, 0))
        self._th_pref_combo.bind("<<ComboboxSelected>>", lambda e: self._load_theater_choices())
        self._th_status = tk.Label(row1, text="", font=self.F["caption"], fg=C["text3"], bg=C["card"])
        self._th_status.pack(side="right")
        row2 = tk.Frame(inp, bg=C["card"])
        row2.pack(fill="x")
        tk.Label(row2, text="映画館", font=self.F["small"], fg=C["text2"], bg=C["card"], width=7, anchor="w").pack(side="left")
        self._th_var = tk.StringVar()
        self._th_combo = ttk.Combobox(row2, textvariable=self._th_var, state="readonly", font=ui_font(10), width=28)
        self._th_combo.pack(side="left", fill="x", expand=True, padx=(6, 8))
        self._btn(row2, "追加", self._add_theater, C["accent"]).pack(side="right")
        self._th_choices = []

        tf = tk.Frame(p, bg=C["bg"])
        tf.pack(fill="both", expand=True, padx=16, pady=(8, 4))
        self.th_tree = ttk.Treeview(tf, style="WL.Treeview", columns=("name", "count", "updated"), show="headings", selectmode="browse", height=4)
        self.th_tree.heading("name", text="映画館")
        self.th_tree.heading("count", text="作品")
        self.th_tree.heading("updated", text="更新")
        self.th_tree.column("name", width=200, minwidth=120)
        self.th_tree.column("count", width=50, minwidth=40, anchor="center")
        self.th_tree.column("updated", width=120, minwidth=80)
        self.th_tree.pack(fill="x")
        self.th_tree.bind("<<TreeviewSelect>>", lambda e: self._refresh_theater_movies_view())
        self.th_tree.bind("<Double-1>", self._on_th_dblclick)
        tk.Label(tf, text="上映作品", font=self.F["caption"], fg=C["text2"], bg=C["bg"]).pack(anchor="w", pady=(8, 2))
        self.th_mv_tree = ttk.Treeview(tf, style="WL.Treeview", columns=("status", "title", "director"), show="headings", selectmode="browse")
        self.th_mv_tree.heading("status", text="状態")
        self.th_mv_tree.heading("title", text="タイトル")
        self.th_mv_tree.heading("director", text="監督")
        self.th_mv_tree.column("status", width=70, minwidth=60, anchor="center")
        self.th_mv_tree.column("title", width=240, minwidth=120)
        self.th_mv_tree.column("director", width=110, minwidth=80)
        sc = ttk.Scrollbar(tf, orient="vertical", command=self.th_mv_tree.yview)
        self.th_mv_tree.configure(yscrollcommand=sc.set)
        self.th_mv_tree.pack(side="left", fill="both", expand=True)
        sc.pack(side="right", fill="y")
        self.th_mv_tree.bind("<Double-1>", self._on_th_movie_dblclick)

        bb = tk.Frame(p, bg=C["bg"])
        bb.pack(fill="x", padx=16, pady=(4, 14))
        self._btn(bb, "削除", self._remove_theater, C["red"]).pack(side="left", padx=(0, 8))
        self._btn(bb, "更新", lambda: self._refresh_theater_movies(force=True), C["bg3"]).pack(side="left")

    def _th_pref_code(self) -> str:
        name = self._th_pref_var.get()
        return next((c for c, n in PREFECTURES.items() if n == name), "13")

    def _load_theater_choices(self):
        pref = self._th_pref_code()
        self._th_status.config(text="館の一覧を取得中…")

        def work():
            lst = fetch.theater_list_cached(pref, APP_DIR)
            err = "" if lst else (fetch.error_text() or "取れませんでした")
            self.root.after(0, lambda: done(lst, err))

        def done(lst, err):
            self._th_choices = lst
            self._th_combo["values"] = [f"{t['area']}: {t['name']} ({t['count']})" for t in lst]
            self._th_status.config(text=f"{len(lst)} 館" if lst else f"⚠ {err}")

        threading.Thread(target=work, daemon=True).start()

    def _add_theater(self):
        i = self._th_combo.current()
        if i < 0 or i >= len(self._th_choices):
            messagebox.showinfo("映画館", "一覧から館を選んでください。", parent=self.root)
            return
        t = self._th_choices[i]
        if any(x.get("url") == t["url"] for x in self.theater_wl):
            self._status(f"追加済み: {t['name']}")
            return
        entry = {"name": t["name"], "url": t["url"], "pref": self._th_pref_code(), "area": t.get("area", ""), "movies": [], "updated": "", "error": ""}
        self.theater_wl.append(entry)
        save_json(THEATER_WL_FILE, self.theater_wl)
        self._refresh_theater_wl()
        self._status(f"追加: {t['name']}")
        self._refresh_theater_movies(only=entry, mark_known=True)

    def _remove_theater(self):
        sel = self.th_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx >= len(self.theater_wl):
            return
        name = self.theater_wl[idx]["name"]
        if not messagebox.askyesno("削除", f"「{name}」を映画館のウォッチリストから削除しますか?", parent=self.root):
            return
        del self.theater_wl[idx]
        save_json(THEATER_WL_FILE, self.theater_wl)
        self._refresh_theater_wl()
        self._status(f"削除: {name}")

    def _refresh_theater_wl(self):
        self.th_tree.delete(*self.th_tree.get_children())
        for i, t in enumerate(self.theater_wl):
            upd = t.get("updated", "")[5:] if t.get("updated") else "—"
            if t.get("error"):
                upd = f"⚠ {t['error']}"
            self.th_tree.insert("", "end", iid=str(i), values=(t["name"], len(t.get("movies", [])), upd))
        if not self.theater_wl:
            self.th_tree.insert("", "end", iid="empty", values=("上の一覧から館を選んで追加すると、ここに並びます", "", ""))
        if self._left_mode == "theater":
            self.wl_cnt.config(text=f"{len(self.theater_wl)}館")
        self._refresh_theater_movies_view()

    def _refresh_theater_movies_view(self):
        self.th_mv_tree.delete(*self.th_mv_tree.get_children())
        sel = self.th_tree.selection()
        if not sel or sel[0] == "empty":
            return
        t = self.theater_wl[int(sel[0])]
        by_id = {info.get("movie_id"): info for info in self.now_showing.values() if info.get("movie_id")}
        for m in t.get("movies", []):
            info = by_id.get(str(m.get("movie_id") or ""), {})
            self.th_mv_tree.insert("", "end", iid=f"{sel[0]}:{m.get('movie_id')}", values=("🟢 上映中", m.get("title", ""), info.get("director", "")))

    def _refresh_theater_movies(self, only: dict | None = None, force: bool = False, mark_known: bool = False, then=None):
        """登録した館の作品を取り直す (別スレッド)。force なら今日のキャッシュを捨てる。mark_known は登録の時 (全部を新着にしない)。"""
        targets = [only] if only else list(self.theater_wl)
        if not targets:
            if then:
                then()
            return
        if force:
            fetch.clear_theaters_cache(APP_DIR)
        self._th_status.config(text="館の作品を取得中…")

        def work():
            results = []
            for t in targets:
                movies = fetch.theater_movies_cached(t["url"], APP_DIR)
                results.append((t, movies, "" if movies else (fetch.error_text() or "取れませんでした")))
            self.root.after(0, lambda: done(results))

        def done(results):
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            for t, movies, err in results:
                if movies:
                    t["movies"], t["updated"], t["error"] = movies, now, ""
                else:
                    t["error"] = err
            save_json(THEATER_WL_FILE, self.theater_wl)
            if mark_known:
                notify.mark_known(APP_DIR, notify.showing_items([], [], dict(self.now_showing), targets))
            self._th_status.config(text=f"{sum(len(t.get('movies', [])) for t in self.theater_wl)} 作品")
            self._refresh_theater_wl()
            self._filter_ns()
            self.refresh_bar()
            if then:
                then()

        threading.Thread(target=work, daemon=True).start()

    def _on_th_dblclick(self, event):
        sel = self.th_tree.selection()
        if sel and sel[0] != "empty":
            webbrowser.open(self.theater_wl[int(sel[0])]["url"])

    def _on_th_movie_dblclick(self, event):
        sel = self.th_mv_tree.selection()
        if not sel:
            return
        mid = sel[0].split(":")[-1]
        title = self.th_mv_tree.item(sel[0], "values")[1]
        if mid.isdigit():
            self._theater_popup(title, mid)

    def _build_movie_wl_view(self, p):
        inp = tk.Frame(p, bg=C["card"], padx=14, pady=10)
        inp.pack(fill="x", padx=16, pady=(8, 0))

        title_row = tk.Frame(inp, bg=C["card"])
        title_row.pack(fill="x", pady=(0, 6))
        tk.Label(title_row, text="タイトル", font=self.F["small"], fg=C["text2"], bg=C["card"], width=7, anchor="w").pack(side="left")
        self.add_entry = AutocompleteEntry(
            title_row, candidates_func=self._get_title_candidates, on_selected=self._on_title_selected,
            async_search_func=self._search_eiga_titles, info_func=self._get_director_for_title,
            alpha=3.0, threshold=0.08,
            font=self.F["normal"], bg=C["bg"], fg=C["text"], insertbackground=C["text"], relief="flat",
            highlightthickness=1, highlightcolor=C["accent"], highlightbackground=C["border"])
        self.add_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(6, 0))
        self._placeholder(self.add_entry, "映画タイトル...")

        dir_row = tk.Frame(inp, bg=C["card"])
        dir_row.pack(fill="x", pady=(0, 4))
        tk.Label(dir_row, text="監督", font=self.F["small"], fg=C["text2"], bg=C["card"], width=7, anchor="w").pack(side="left")
        self.dir_entry = AutocompleteEntry(
            dir_row, candidates_func=self._get_director_candidates, async_search_func=self._search_eiga_directors,
            alpha=2.5, threshold=0.06,
            font=self.F["normal"], bg=C["bg"], fg=C["text"], insertbackground=C["text"], relief="flat",
            highlightthickness=1, highlightcolor=C["accent"], highlightbackground=C["border"])
        self.dir_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(6, 8))
        self._placeholder(self.dir_entry, "監督名...")
        self._btn(dir_row, "追加", self._add_movie, C["accent"]).pack(side="right")
        self.add_entry.bind("<Return>", lambda e: self.dir_entry.focus_set())
        self.dir_entry.bind("<Return>", lambda e: self._add_movie())

        tf = tk.Frame(p, bg=C["bg"])
        tf.pack(fill="both", expand=True, padx=16, pady=(8, 4))
        self.wl_tree = ttk.Treeview(tf, style="WL.Treeview", columns=("status", "title", "director", "info"),
                                    show="headings", selectmode="browse")
        self.wl_tree.heading("status", text="状態")
        self.wl_tree.heading("title", text="タイトル")
        self.wl_tree.heading("director", text="監督")
        self.wl_tree.heading("info", text="")
        self.wl_tree.column("status", width=104, minwidth=90, anchor="center")   # 『📅 11/14 配信』が入る幅 (MV-22)
        self.wl_tree.column("title", width=200, minwidth=120)
        self.wl_tree.column("director", width=120, minwidth=80)
        self.wl_tree.column("info", width=90, minwidth=60, anchor="center")
        sc = ttk.Scrollbar(tf, orient="vertical", command=self.wl_tree.yview)
        self.wl_tree.configure(yscrollcommand=sc.set)
        self.wl_tree.pack(side="left", fill="both", expand=True)
        sc.pack(side="right", fill="y")
        self.wl_tree.bind("<Double-1>", self._on_wl_dblclick)

        bb = tk.Frame(p, bg=C["bg"])
        bb.pack(fill="x", padx=16, pady=(4, 14))
        self._btn(bb, "削除", self._remove_movie, C["red"]).pack(side="left", padx=(0, 8))
        self._btn(bb, "上映チェック", self._check_status, C["accent"]).pack(side="left")
        self._btn(bb, "eiga.com", lambda: webbrowser.open("https://eiga.com/now/"), C["bg3"]).pack(side="right")

    def _build_dir_wl_view(self, p):
        tk.Label(p, text="監督名を追加すると、eiga.comから全作品を自動取得します", font=self.F["caption"],
                 fg=C["text3"], bg=C["bg"]).pack(fill="x", padx=16, pady=(8, 0))

        inp = tk.Frame(p, bg=C["card"], padx=14, pady=10)
        inp.pack(fill="x", padx=16, pady=(6, 0))
        self.dir_wl_entry = AutocompleteEntry(
            inp, candidates_func=self._get_all_director_candidates, on_selected=self._on_dir_wl_entry_selected,
            async_search_func=self._search_eiga_directors, alpha=2.5, threshold=0.06,
            font=self.F["normal"], bg=C["bg"], fg=C["text"], insertbackground=C["text"], relief="flat",
            highlightthickness=1, highlightcolor=C["accent"], highlightbackground=C["border"])
        self.dir_wl_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 8))
        self._placeholder(self.dir_wl_entry, "監督名を入力...")
        self._btn(inp, "追加", self._add_dir_to_wl, C["accent"]).pack(side="right")
        self.dir_wl_entry.bind("<Return>", lambda e: self._add_dir_to_wl())

        ff = tk.Frame(p, bg=C["bg"])
        ff.pack(fill="x", padx=16, pady=(8, 2))
        tk.Label(ff, text="絞り込み:", font=self.F["caption"], fg=C["text2"], bg=C["bg"]).pack(side="left", padx=(0, 6))
        self._dir_wl_filter_var = tk.StringVar(value="── すべて ──")
        self._dir_wl_filter_var.trace_add("write", lambda *a: self._refresh_dir_wl())
        self.dir_wl_combo = ttk.Combobox(ff, textvariable=self._dir_wl_filter_var, state="readonly",
                                         font=ui_font(10), width=20)
        self.dir_wl_combo.pack(side="left")
        self._dir_wl_status = tk.Label(ff, text="", font=self.F["caption"], fg=C["text3"], bg=C["bg"])
        self._dir_wl_status.pack(side="right")

        tf = tk.Frame(p, bg=C["bg"])
        tf.pack(fill="both", expand=True, padx=16, pady=(4, 4))
        self.dir_wl_tree = ttk.Treeview(tf, style="WL.Treeview", columns=("status", "title", "director"),
                                        show="headings", selectmode="browse")
        self.dir_wl_tree.heading("status", text="状態")
        self.dir_wl_tree.heading("title", text="タイトル")
        self.dir_wl_tree.heading("director", text="監督")
        self.dir_wl_tree.column("status", width=80, minwidth=60, anchor="center")
        self.dir_wl_tree.column("title", width=240, minwidth=120)
        self.dir_wl_tree.column("director", width=120, minwidth=80)
        sc = ttk.Scrollbar(tf, orient="vertical", command=self.dir_wl_tree.yview)
        self.dir_wl_tree.configure(yscrollcommand=sc.set)
        self.dir_wl_tree.pack(side="left", fill="both", expand=True)
        sc.pack(side="right", fill="y")
        self.dir_wl_tree.bind("<Double-1>", self._on_dir_wl_dblclick)

        bb = tk.Frame(p, bg=C["bg"])
        bb.pack(fill="x", padx=16, pady=(4, 14))
        self._btn(bb, "監督を削除", self._remove_dir_from_wl, C["red"]).pack(side="left", padx=(0, 8))
        self._btn(bb, "上映チェック", self._check_dir_wl_status, C["accent"]).pack(side="left")
        self._btn(bb, "劇場", self._theater_for_dir_wl, C["bg3"]).pack(side="left", padx=(8, 0))
        self._btn(bb, "作品再取得", self._refetch_dir_filmography, C["bg3"]).pack(side="right")

    def _build_right(self, p):
        self._build_picks(p)
        hdr = tk.Frame(p, bg=C["bg"])
        hdr.pack(fill="x", padx=16, pady=(16, 0))
        tk.Label(hdr, text="上映中", font=self.F["h2"], fg=C["text"], bg=C["bg"]).pack(side="left")
        self.ns_upd = tk.Label(hdr, text="", font=self.F["caption"], fg=C["text3"], bg=C["bg"])
        self.ns_upd.pack(side="right")
        self.ns_cnt = tk.Label(hdr, text="", font=self.F["caption"], fg=C["text3"], bg=C["bg"])
        self.ns_cnt.pack(side="right", padx=(0, 10))
        self._build_ns_view(p)

    def _build_ns_view(self, p):
        sf = tk.Frame(p, bg=C["bg"])
        sf.pack(fill="x", padx=16, pady=(8, 0))
        self.search_var = tk.StringVar()
        se = tk.Entry(sf, textvariable=self.search_var, font=self.F["normal"], bg=C["card"], fg=C["text"],
                      insertbackground=C["text"], relief="flat", highlightthickness=1,
                      highlightcolor=C["accent"], highlightbackground=C["border"])
        se.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        self._placeholder(se, "タイトルまたは監督名で検索...")
        self.search_entry = se
        self._btn(sf, "更新", self._fetch_movies, C["bg3"]).pack(side="right")
        self.sort_btn = self._btn(sf, "おすすめ順", self._toggle_ns_sort, C["bg3"])
        self.sort_btn.pack(side="right", padx=(0, 8))

        tf = tk.Frame(p, bg=C["bg"])
        tf.pack(fill="both", expand=True, padx=16, pady=(8, 4))
        self.ns_tree = ttk.Treeview(tf, style="NS.Treeview", columns=("no", "star", "title", "director"),
                                    show="headings", selectmode="browse")
        self.ns_tree.heading("no", text="#")
        self.ns_tree.heading("star", text="★", command=self._toggle_ns_sort)
        self.ns_tree.heading("title", text="タイトル")
        self.ns_tree.heading("director", text="監督")
        self.ns_tree.column("no", width=36, minwidth=30, anchor="center")
        self.ns_tree.column("star", width=44, minwidth=36, anchor="center")
        self.ns_tree.column("title", width=290, minwidth=160)
        self.ns_tree.column("director", width=130, minwidth=80)
        sc = ttk.Scrollbar(tf, orient="vertical", command=self.ns_tree.yview)
        self.ns_tree.configure(yscrollcommand=sc.set)
        self.ns_tree.pack(side="left", fill="both", expand=True)
        sc.pack(side="right", fill="y")
        self.ns_tree.bind("<Double-1>", self._on_ns_dblclick)
        self.ns_tree.bind("<Button-3>", self._on_ns_rclick)
        self.ns_tree.bind("<<TreeviewSelect>>", self._on_ns_select)
        self.search_var.trace_add("write", lambda *a: self._filter_ns())

        bb = tk.Frame(p, bg=C["bg"])
        bb.pack(fill="x", padx=16, pady=(4, 14))
        self._btn(bb, "追加", self._add_from_ns, C["accent"]).pack(side="left")
        self._btn(bb, "劇場", self._theater_for_ns, C["bg3"]).pack(side="left", padx=(8, 0))

    def _btn(self, parent, text, cmd, bg):
        fg = "white" if bg not in (C["bg3"],) else C["text2"]
        return Button(parent, text=text, font=self.F["btn"], bg=bg, fg=fg, activebackground=C["accent_hover"],
                         activeforeground="white", relief="flat", cursor="hand2", command=cmd, padx=14, pady=6, bd=0)

    def _placeholder(self, entry, text):
        entry._ph = text
        entry._has_ph = True
        entry.insert(0, text)
        entry.config(fg=C["text3"])

        def fi(e):
            if entry._has_ph:
                entry.delete(0, "end")
                entry.config(fg=C["text"])
                entry._has_ph = False

        def fo(e):
            if not entry.get().strip():
                entry.insert(0, entry._ph)
                entry.config(fg=C["text3"])
                entry._has_ph = True

        entry.bind("<FocusIn>", fi)
        entry.bind("<FocusOut>", fo)

    # ---- 版と置き場 (MV-4) ----
    def _about_text(self) -> str:
        bi = paths.build_info(_BUNDLE_DIR)
        lines = [f"映画上映チェッカー {__version__}"]
        if bi:
            lines.append(f"固めた日: {bi.get('built', '?')}  ({bi.get('python', '?')})")
        else:
            lines.append("源から動かしています (固めていない)")
        lines.append(f"データの置き場: {APP_DIR}")
        lines.append(f"記録: {APP_DIR / 'notify_log.json'}")
        lines.append(f"外観: {self.settings.get('theme', 'dark')} (ヘッダの『外観』で dark / light)")
        if _FROZEN and APP_DIR != EXE_DIR:
            lines.append(f"exe: {EXE_DIR}")
        return "\n".join(lines)

    def _open_about(self):
        win = tk.Toplevel(self.root)
        win.title("情報")
        win.configure(bg=C["card"])
        win.resizable(False, False)
        win.transient(self.root)
        tk.Label(win, text=self._about_text(), font=self.F["small"], fg=C["text"], bg=C["card"], justify="left").pack(padx=20, pady=(16, 8), anchor="w")
        bf = tk.Frame(win, bg=C["card"])
        bf.pack(fill="x", padx=20, pady=(0, 16))
        Button(bf, text="置き場を開く", font=self.F["small"], bg=C["bg3"], fg=C["text2"], relief="flat", cursor="hand2",
                  padx=10, pady=3, bd=0, command=lambda: open_folder(APP_DIR)).pack(side="left")
        Button(bf, text="閉じる", font=self.F["small"], bg=C["accent"], fg="white", relief="flat", cursor="hand2",
                  padx=14, pady=3, bd=0, command=win.destroy).pack(side="right")
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + 120
        win.geometry(f"+{x}+{y}")

    def _maybe_import_old_data(self):
        """初回 (置き場に json が 1 つも無い) に exe の隣の json があれば黙って写す (MV-16)。問いは出さない。元は消さない。"""
        was_empty = paths.is_empty(APP_DIR)      # settings.json を書く前に見る (書くと空でなくなる)
        if not SETTINGS_FILE.exists():           # 置き場が決まった印 (次回から『初回』にならない)
            self.save_settings()
        if not _FROZEN or APP_DIR == EXE_DIR or not was_empty:
            return
        if not any((EXE_DIR / n).is_file() for n in paths.DATA_FILES):
            return
        copied, notes = paths.import_from(EXE_DIR, APP_DIR)
        if copied:
            self.watchlist = load_json(WATCHLIST_FILE)
            self.dir_watchlist = load_json(DIR_WL_FILE)
            self.settings = load_json(SETTINGS_FILE) or self.settings
            if "pref_codes" not in self.settings:
                self.settings["pref_codes"] = ["13"]
            self._update_pref_label()
            self._refresh_wl()
        msg = f"取り込み: {len(copied)} 件 (exe の隣から)" if copied else "取り込み: 無し"
        if notes:
            msg += " / " + " / ".join(notes)
        self._status(msg)
        if copied:
            self.save_settings()

    def _show_store_notes(self):
        """json が壊れていて退避から戻した等の訳 (moviecheck.store) を状態欄に。"""
        notes = list(store.last_notes.values())
        if notes:
            self._status("⚠ " + " / ".join(notes))
            store.last_notes.clear()

    def _fetch_failed_text(self) -> str:
        """取得が 0 件だった時の訳 (moviecheck.fetch.last_error)。無ければ空。"""
        return fetch.error_text()

    def _status(self, msg):
        self.status_lbl.config(text=msg, fg=C["hdr_fg"] if msg.startswith("⚠") else C["hdr_fg2"])
        self.root.update_idletasks()

    def _show_progress(self, show=True):
        if show:
            self.progress.pack(fill="x", side="top", before=self._main_pw)
        else:
            self.progress.pack_forget()

    def _get_title_candidates(self) -> list[str]:
        titles = list(self.now_showing.keys())
        titles.extend(t for t in self._eiga_search_cache.keys() if t not in self.now_showing)
        return titles

    def _get_director_for_title(self, title: str) -> str:
        info = self.now_showing.get(title)
        if info and info.get("director"):
            return info["director"]
        cached = self._eiga_search_cache.get(title)
        if cached and cached.get("director"):
            return cached["director"]
        return ""

    def _search_eiga_titles(self, query: str) -> list[str]:
        """eiga.com を検索して題名を返す (別スレッドから)。監督の無い物は先頭 8 件だけ作品ページから取る。"""
        results = search_eiga(query)
        titles = []
        for r in results:
            t = r["title"]
            if t not in self._eiga_search_cache:
                self._eiga_search_cache[t] = r
            titles.append(t)
        need_director = [
            r for r in results
            if r.get("movie_id")
            and not self._eiga_search_cache.get(r["title"], {}).get("director")
            and not self.now_showing.get(r["title"], {}).get("director")
        ][:8]
        for r in need_director:
            try:
                d = fetch_movie_director(r["movie_id"])
                if d:
                    self._eiga_search_cache[r["title"]]["director"] = d
            except Exception:
                continue
        return titles

    def _get_director_candidates(self) -> list[str]:
        dirs = set()
        for info in self.now_showing.values():
            d = info.get("director", "")
            if d:
                for name in d.split(", "):
                    name = name.strip()
                    if name:
                        dirs.add(name)
        for w in self.watchlist:
            d = w.get("director", "")
            if d:
                dirs.add(d)
        for dw in self.dir_watchlist:
            dirs.add(dw["name"])
        return sorted(dirs)

    def _on_title_selected(self, title: str):
        """題名の候補を選んだら監督を埋める (手元に無ければ作品ページから取る)。"""
        info = self.now_showing.get(title)
        if info and info.get("director"):
            self._fill_director(info["director"])
            return
        cached = self._eiga_search_cache.get(title)
        if cached and cached.get("director"):
            self._fill_director(cached["director"])
            return
        movie_id = ""
        if cached:
            movie_id = cached.get("movie_id", "")
        elif info:
            movie_id = info.get("movie_id", "")
        if movie_id:
            self._status("🔍 監督情報を取得中...")

            def work():
                d = fetch_movie_director(movie_id)
                self.root.after(0, lambda: self._on_director_fetched(title, d))

            threading.Thread(target=work, daemon=True).start()

    def _on_director_fetched(self, title: str, director: str):
        if director:
            if title in self._eiga_search_cache:
                self._eiga_search_cache[title]["director"] = director
            self._fill_director(director)
            self._status(f"✅ 監督: {director}")
        else:
            why = self._fetch_failed_text()
            self._status("監督情報が見つかりませんでした" + (f" ({why})" if why else ""))

    def _fill_director(self, director: str):
        self.dir_entry.delete(0, "end")
        self.dir_entry._has_ph = False
        self.dir_entry.insert(0, director)
        self.dir_entry.config(fg=C["text"])

    def _update_pref_label(self):
        codes = self.pref_codes
        if not codes:
            self.pref_label.config(text="未選択")
            return
        names = [PREFECTURES.get(c, c) for c in codes]
        if len(names) <= 3:
            self.pref_label.config(text=", ".join(names))
        else:
            self.pref_label.config(text=f"{', '.join(names[:2])} 他{len(names) - 2}地域")

    # ---- メール通知 (MV-2) ----
    C = C  # mail_dialog が app.C / app.F で色と字体を使う

    def save_settings(self):
        save_json(SETTINGS_FILE, self.settings)

    def _toggle_theme(self):
        """MV-20: dark ⇄ light。pack した物を全部捨てて _build_ui からやり直す (Toplevel は残す)。取得中は断る。"""
        if self.is_fetching:
            self._status("取得中は外観を変えられません。少し待ってから")
            return
        name = "light" if self.settings.get("theme", "dark") == "dark" else "dark"
        t0 = time.perf_counter()
        self.settings["theme"] = name
        self.save_settings()
        C.clear()
        C.update(THEMES[name])
        self.root.configure(bg=C["bg"])
        for w in self.root.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        self._setup_styles()
        self._build_ui()
        self._refresh_wl()
        if self.now_showing:
            self._filter_ns()
            self._refresh_picks()
            self.ns_upd.config(text=f"更新: {self._last_fetch.get('ts', '')}" + (" (キャッシュ)" if self._last_fetch.get("src") == "キャッシュ" else ""))
        self.refresh_bar()
        self._status(f"外観: {name} ({int((time.perf_counter() - t0) * 1000)} ms)")

    def _open_mail_settings(self):
        MailSettingsDialog(self)

    def run_notify(self, kind: str = "auto") -> dict:
        """別スレッドから呼ぶ。kind = auto | digest | test。戻りは notify.run の物。"""
        return notify.run(self.settings, APP_DIR, list(self.watchlist), list(self.dir_watchlist), dict(self.now_showing),
                          kind=kind, theaters_fn=lambda mid, prefs: fetch_theaters_multi(mid, prefs, APP_DIR),
                          picks_fn=lambda: self._picks_for_mail(dict(self.now_showing), list(self.watchlist), list(self.dir_watchlist), list(self.theater_wl)),
                          theater_wl=list(self.theater_wl),
                          coming=notify.coming_items(list(self.watchlist), list(self._coming), datetime.now().strftime("%Y-%m-%d")))

    def data_dir(self) -> Path:
        return APP_DIR

    def send_mail_command(self) -> str:
        """タスク スケジューラに登録する 1 行 (exe か python + 源)。"""
        if _FROZEN:
            return f'"{sys.executable}" --send-mail'
        return f'"{sys.executable}" "{Path(__file__).resolve()}" --send-mail'

    # ---- 近道と窓の記憶 (MV-14、HIG macOS) ----
    def _bind_shortcuts(self):
        r = self.root
        r.bind_all("<Control-f>", lambda e: self._focus_search())
        r.bind_all("<Control-F>", lambda e: self._focus_search())
        if _MAC:  # Mac は ⌘F も
            r.bind_all("<Command-f>", lambda e: self._focus_search())
            r.bind_all("<Command-F>", lambda e: self._focus_search())
        r.bind_all("<F5>", lambda e: self._fetch_movies())
        r.bind_all("<Delete>", self._on_delete_key)
        r.bind_all("<Escape>", self._on_escape)

    def _focus_search(self):
        e = getattr(self, "search_entry", None)
        if e is None:
            return
        e.focus_set()
        if getattr(e, "_has_ph", False):
            e.delete(0, "end")
            e.config(fg=C["text"])
            e._has_ph = False

    def _on_delete_key(self, event=None):
        """焦点が WL の表にある時だけ削除 (入力欄の Delete は字の削除のまま)。"""
        w = self.root.focus_get()
        if w is getattr(self, "wl_tree", None):
            self._remove_movie()
        elif w is getattr(self, "dir_wl_tree", None):
            self._remove_dir_from_wl()
        elif w is getattr(self, "th_tree", None):
            self._remove_theater()

    def _on_escape(self, event=None):
        """一番手前の窓 (Toplevel) を閉じる。地域の窓は自分で Esc を持つ。"""
        tops = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_viewable()]
        if tops:
            tops[-1].destroy()

    def _restore_window(self):
        g = str(self.settings.get("window") or "")
        try:
            size, x, y = g.split("+")
            w, h = (int(v) for v in size.split("x"))
            x, y = int(x), int(y)
        except ValueError:
            return
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if 0 <= x < sw - 200 and 0 <= y < sh - 200 and w >= 1000 and h >= 600:
            self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _on_close(self):
        try:
            self.settings["window"] = self.root.geometry()
            self.save_settings()
        except Exception:  # noqa: BLE001 — 閉じる邪魔はしない
            pass
        self.root.destroy()

    # ---- 状況の帯 (MV-7) ----
    def refresh_bar(self):
        """題名の下の 1 行。主スレッドから呼ぶ (別スレッドは root.after で)。"""
        parts = []
        if self._fetch_page:
            p, n = self._fetch_page
            parts.append(f"📡 上映中 {p}/{fetch.TOTAL_PAGES} ページ ({n} 件)…")
        elif self._last_fetch.get("error"):
            prev = f" · 前回 {self._last_fetch['ts']} のまま" if self._last_fetch.get("ts") else ""
            parts.append(f"取得 失敗 ({self._last_fetch['error']}){prev}")
        elif self._last_fetch.get("ts"):
            parts.append(f"取得 {self._last_fetch['ts'][5:]} ({self._last_fetch['src']})")
        else:
            parts.append("取得 まだ")
        if self._next_update:
            parts.append(f"次の自動更新 {self._next_update.strftime('%H:%M')}")
        cfg = notify.cfg_of(self.settings)
        if cfg.get("to"):
            entries = notify.recent_log(APP_DIR, 20)
            last = next((e for e in entries if e.get("kind") != "skip"), None)
            state = load_json(APP_DIR / "notify_state.json") or {}
            nd = notify.next_digest(cfg, state if isinstance(state, dict) else {}, datetime.now())
            m = "メール: 最後 " + (notify.log_line(last)[5:] if last else "まだ")
            if nd:
                m += f" / 次のまとめ {nd.strftime('%m/%d')} ({notify.WEEKDAYS[nd.weekday()]}) {nd.hour} 時"
            parts.append(m)
        else:
            parts.append("メール: 未設定")
        if self.theater_wl:
            parts.append(f"映画館 {len(self.theater_wl)} 館 {sum(len(x.get('movies', [])) for x in self.theater_wl)} 作品")
        t = self.taste.tally()
        glow = picks.afterglow(self.taste.feedback, self.now_showing)
        if glow:
            parts.append(glow)
        else:
            parts.append(f"好み: 反応 {t['n']}" if t["n"] else "好み: まだ学んでいない")
        self.bar_lbl.config(text="  ·  ".join(parts))

    # ---- 好みの学習 (MV-8) ----
    def _picks_for_mail(self, now_showing: dict, wl: list, dwl: list, thw: list | None = None):
        """まとめメールの頭 (MV-10): おまかせで 3 本と余韻。self が None でも動く (--send-mail)。"""
        model = self.taste if self is not None else taste.Model(TASTE_FILE)
        today = datetime.now().strftime("%Y-%m-%d")
        reasons = picks.wl_showing_titles(wl, dwl, now_showing, thw)
        chosen = picks.choose(list(now_showing.values()), reasons, model, picks.load_dismissed(APP_DIR, today))
        return chosen, picks.afterglow(model.feedback, now_showing)

    def _taste_feedback(self, movie: dict, kind: str):
        rank = None
        if movie.get("title") in self.now_showing:
            rank = list(self.now_showing).index(movie["title"]) + 1
        self.taste.feedback_on(movie, kind, rank)
        self._filter_ns()
        self.refresh_bar()
        self._status(f"学習: 「{movie.get('title', '')}」{taste.LABELS[kind]}")

    def _toggle_ns_sort(self):
        self._ns_sort = "taste" if self._ns_sort == "rank" else "rank"
        self.sort_btn.config(text="人気順" if self._ns_sort == "taste" else "おすすめ順")
        if self._ns_sort == "taste" and not self.taste.learned():
            self._status("まだ学んでいません。追加か右クリックで反応を")
        self._filter_ns()

    def _on_ns_select(self, event=None):
        sel = self.ns_tree.selection()
        if not sel or sel[0] not in self._ns_scores:
            return
        s = self._ns_scores[sel[0]]
        if s.get("why"):
            self._status(f"理由: {s['why']} ({s['score']:+.1f})")

    def _on_ns_rclick(self, event):
        iid = self.ns_tree.identify_row(event.y)
        if not iid:
            return
        self.ns_tree.selection_set(iid)
        title, info, is_ns = self._get_ns_item_info(iid)
        movie = {"title": title, "director": info.get("director", "")}
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="追加", command=self._add_from_ns)
        menu.add_separator()
        menu.add_command(label="観た: 良かった", command=lambda: self._taste_feedback(movie, "good"))
        menu.add_command(label="観た: いまいち", command=lambda: self._taste_feedback(movie, "bad"))
        menu.add_command(label="興味なし", command=lambda: self._taste_feedback(movie, "skip"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_taste(self):
        win = tk.Toplevel(self.root)
        win.title("好み")
        win.configure(bg=C["bg"])
        win.transient(self.root)
        f = tk.Frame(win, bg=C["bg"], padx=16, pady=12)
        f.pack(fill="both", expand=True)
        t = self.taste.tally()
        glow = picks.afterglow(self.taste.feedback, self.now_showing)
        if glow:
            tk.Label(f, text=glow, font=self.F["h2"], fg=C["text"], bg=C["bg"]).pack(anchor="w", pady=(0, 6))
        tk.Label(f, text=f"反応 {t['n']} 件 (👍 {t['up']} / 👎 {t['down']})。外には送りません。間違った学習は『消す』で戻せます。",
                 font=self.F["small"], fg=C["text2"], bg=C["bg"], wraplength=520, justify="left").pack(anchor="w", pady=(0, 8))
        cols = tk.Frame(f, bg=C["bg"])
        cols.pack(fill="both", expand=True)
        likes, dislikes = self.taste.top(10)

        def column(parent, head, rows):
            fr = tk.Frame(parent, bg=C["card"], padx=10, pady=8, highlightthickness=1, highlightbackground=C["border"])
            fr.pack(side="left", fill="both", expand=True, padx=4)
            tk.Label(fr, text=head, font=self.F["btn"], fg=C["text"], bg=C["card"]).pack(anchor="w")
            if not rows:
                tk.Label(fr, text="(まだ無い)", font=self.F["small"], fg=C["text3"], bg=C["card"]).pack(anchor="w")
            for feat, v in rows:
                r = tk.Frame(fr, bg=C["card"])
                r.pack(fill="x", pady=1)
                tk.Label(r, text=f"{taste.feature_label(feat)}  {v:+.1f}", font=self.F["small"], fg=C["text2"], bg=C["card"]).pack(side="left")
                Button(r, text="消す", font=self.F["caption"], bg=C["bg3"], fg=C["text2"], relief="flat", bd=0, padx=6, cursor="hand2",
                          command=lambda ft=feat: (self.taste.forget(ft), win.destroy(), self._open_taste(), self._filter_ns())).pack(side="right")

        column(cols, "好き (重みの上位)", likes)
        column(cols, "嫌い (重みの下位)", dislikes)
        tk.Label(f, text="仕組み: 監督・題名の語・人気の帯を特徴にした線形バンディット。WL 追加 +1、👍 +2、👎 −1、興味なし −1 で重みを少しずつ動かし、たまに 🎲 で未知の物を出します。",
                 font=self.F["caption"], fg=C["text3"], bg=C["bg"], wraplength=520, justify="left").pack(anchor="w", pady=(8, 0))
        Button(f, text="閉じる", font=self.F["small"], bg=C["accent"], fg="white", relief="flat", bd=0, padx=14, pady=3, cursor="hand2", command=win.destroy).pack(anchor="e", pady=(8, 0))

    def _maybe_notify(self):
        """取得の後: 変化があれば (か、まとめの日なら) 送る。設定が無ければ何もしない。"""
        if not (self.settings.get("mail") or {}).get("to") or not self.now_showing:
            return

        def work():
            r = self.run_notify("auto")
            self.root.after(0, lambda: done(r))

        def done(r):
            if r.get("error"):
                self._status(f"⚠ メール: {r['error']}")
            elif r.get("sent"):
                self._status(f"✉ メールを送りました ({r['count']} 件)")
            self.refresh_bar()

        threading.Thread(target=work, daemon=True).start()

    def _open_region_selector(self):
        RegionSelector(self.root, self.pref_codes, self._on_regions_saved, F=self.F)

    def _on_regions_saved(self, codes: list[str]):
        self.settings["pref_codes"] = codes
        save_json(SETTINGS_FILE, self.settings)
        self._update_pref_label()
        names = [PREFECTURES.get(c, c) for c in codes]
        self._status(f"📍 地域を変更: {', '.join(names)}")

    @property
    def pref_codes(self) -> list[str]:
        return self.settings.get("pref_codes", ["13"])

    # ---- 取得 (起動時・自動・手動) ----
    def _startup_fetch(self):
        cache = load_json(CACHE_FILE) if CACHE_FILE.exists() else {}
        if cache and cache.get("movies"):
            try:
                last_dt = datetime.strptime(cache["updated"], "%Y-%m-%d %H:%M")
                has_synopsis = any(m.get("synopsis") for m in cache["movies"])
                if datetime.now() - last_dt < timedelta(hours=6) and has_synopsis:   # 古い形 (あらすじ無し) は取り直す (MV-12)
                    self.now_showing = {m["title"]: m for m in cache["movies"]}
                    self._on_data_ready(cache=True, ts=cache["updated"])
                    if self.watchlist:
                        self._do_check(silent=True)
                    self._maybe_notify()
                    self._schedule_update()
                    return
            except (ValueError, TypeError, KeyError):
                pass
        self._fetch_movies()

    def _schedule_update(self):
        if self._timer:
            self.root.after_cancel(self._timer)
        self._timer = self.root.after(21600000, self._auto_update)  # 6 時間
        self._next_update = datetime.now() + timedelta(hours=6)
        self.refresh_bar()

    def _auto_update(self):
        if self.is_fetching:
            self._schedule_update()
            return
        self._status("📡 自動更新中...")
        self.is_fetching = True

        def work():
            ms = fetch_now_showing()
            d = _merge_ranking(ms, fetch_ranking())
            self._coming = fetch.coming_cached(APP_DIR)   # MV-22
            self.root.after(0, lambda: self._on_auto_done(d))

        threading.Thread(target=work, daemon=True).start()

    def _on_auto_done(self, movies):
        self.now_showing = movies
        self.is_fetching = False
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save_cache(ts)
        self._on_data_ready(cache=False, ts=ts)
        if self.watchlist:
            self._do_check(silent=True)
        self._maybe_notify()
        self._schedule_update()

    def _fetch_movies(self):
        if self.is_fetching:
            return
        fetch.clear_theaters_cache(APP_DIR)   # 取り直しの意思 (MV-11)
        self.is_fetching = True
        self._status("📡 eiga.com から映画情報を取得中...")
        self._show_progress(True)
        self.progress["value"] = 0

        def work():
            def prog(p, t):
                self.root.after(0, lambda: self._on_prog(p, t))

            ms = fetch_now_showing(progress_cb=prog)
            d = _merge_ranking(ms, fetch_ranking())
            self._coming = fetch.coming_cached(APP_DIR)   # MV-22
            self.root.after(0, lambda: self._on_fetch_done(d))

        threading.Thread(target=work, daemon=True).start()

    def _on_prog(self, page, total):
        self.progress["value"] = page
        self._status(f"📡 取得中... ページ {page} ({total}件)")
        self._fetch_page = (page, total)
        self.refresh_bar()

    def _fetch_got_nothing(self, movies) -> bool:
        """取得が空で訳がある時: 前の一覧とキャッシュは守り、訳だけ状態欄に出す (v3.0 は空で上書きしていた)。"""
        if movies or not self._fetch_failed_text():
            return False
        self.is_fetching = False
        self._show_progress(False)
        self._status(f"⚠ 0件: {self._fetch_failed_text()}")
        self._last_fetch["error"] = self._fetch_failed_text()
        self._fetch_page = None
        self._schedule_update()
        self.refresh_bar()
        return True

    def _on_fetch_done(self, movies):
        if self._fetch_got_nothing(movies):
            return
        self.now_showing = movies
        self.is_fetching = False
        self._show_progress(False)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save_cache(ts)
        self._on_data_ready(cache=False, ts=ts)
        if self.watchlist:
            self._do_check(silent=True)
        self._refresh_theater_movies(then=self._maybe_notify)   # 館の作品 (MV-19) を取ってから通知
        self._schedule_update()

    def _on_data_ready(self, cache=False, ts=""):
        self._last_fetch = {"ts": ts, "src": "キャッシュ" if cache else "取得", "error": ""}
        self._fetch_page = None
        self.refresh_bar()
        self.ns_cnt.config(text=f"{len(self.now_showing)}件")
        self.ns_upd.config(text=f"更新: {ts}" + (" (キャッシュ)" if cache else ""))
        src = "キャッシュ利用" if cache else "取得完了"
        why = "" if cache or self.now_showing else self._fetch_failed_text()
        if why:
            self._status(f"⚠ 0件: {why}")
        else:
            self._status(f"✅ {len(self.now_showing)}件 {src} ({ts})")
        self._filter_ns()
        if self._left_mode == "director":
            self._refresh_dir_wl()
        self._refresh_picks()

    def _save_cache(self, ts):
        save_json(CACHE_FILE, {"updated": ts, "movies": list(self.now_showing.values())})
        fetch.prune_posters(POSTERS_DIR, [m.get("movie_id") for m in self.now_showing.values()])

    # ---- おすすめ (MV-9: 景色・行動・余韻) ----
    PICK_N_CYCLE = (3, 6, 9, 12)
    CARD_W, IMG_W, IMG_H = 200, 184, 104

    def _build_picks(self, p):
        self.picks_frame = tk.Frame(p, bg=C["bg"])
        self.picks_frame.pack(fill="x", padx=16, pady=(16, 0))
        top = tk.Frame(self.picks_frame, bg=C["bg"])
        top.pack(fill="x")
        tk.Label(top, text="おすすめ", font=self.F["h2"], fg=C["text"], bg=C["bg"]).pack(side="left")
        self.picks_toggle = self._flat(top, "隠す", self._toggle_picks)
        self.picks_toggle.pack(side="right")
        self._flat(top, "次の候補", self._next_picks).pack(side="right", padx=(0, 8))
        self.picks_n_btn = self._flat(top, f"{self._picks_n()} 件", self._cycle_picks_n)
        self.picks_n_btn.pack(side="right", padx=(0, 8))
        self._flat(top, "›", lambda: self._scroll_rail(1)).pack(side="right", padx=(0, 8))
        self._flat(top, "‹", lambda: self._scroll_rail(-1)).pack(side="right", padx=(0, 2))
        # 気分の札の行 (MV-20: ポップアップをやめて常に見せる。件数つき、選んだ物は青)
        self.mood_row = tk.Frame(self.picks_frame, bg=C["bg"])
        self.mood_row.pack(fill="x", pady=(8, 0))
        self.mood_btns = {}
        for mood in picks.MOODS:
            b = Button(self.mood_row, text=mood, font=self.F["small"], relief="flat", bd=0, padx=10, pady=3, cursor="hand2",
                          activebackground=C["border"], activeforeground=C["text"], disabledforeground=C["text3"],
                          command=lambda m=mood: self._set_mood(m))
            b.pack(side="left", padx=(0, 4))
            self.mood_btns[mood] = b
        self.picks_note = tk.Label(self.picks_frame, text="", font=self.F["caption"], fg=C["text3"], bg=C["bg"], anchor="w")
        self.picks_note.pack(fill="x", pady=(4, 0))
        self._paint_moods()
        # 札は横一列 (Canvas の中の Frame)。‹ › と滚轮で 1 枚ずつ送る
        self._blank_img = tk.PhotoImage(width=self.IMG_W, height=self.IMG_H)
        self.rail = tk.Canvas(self.picks_frame, bg=C["bg"], highlightthickness=0, height=self.IMG_H + 150, xscrollincrement=self.CARD_W + 8)
        self.cards_frame = tk.Frame(self.rail, bg=C["bg"])
        self.rail.create_window((0, 0), window=self.cards_frame, anchor="nw")
        self.cards_frame.bind("<Configure>", self._on_rail_configure)
        self.rail.bind("<MouseWheel>", self._on_rail_wheel)
        self.rail.pack(fill="x", pady=(6, 0))
        if self.settings.get("picks_hidden"):
            self._toggle_picks(force_hidden=True)

    def _flat(self, parent, text, cmd):
        return Button(parent, text=text, font=self.F["btn"], bg=C["bg3"], fg=C["text2"], activebackground=C["border"], activeforeground=C["text"],
                         relief="flat", bd=0, padx=12, pady=4, cursor="hand2", command=cmd)

    def _on_rail_configure(self, e=None):
        self.rail.configure(scrollregion=self.rail.bbox("all"))
        h = self.cards_frame.winfo_reqheight()
        if h > 1:
            self.rail.configure(height=h)

    def _on_rail_wheel(self, e):
        self.rail.xview_scroll(wheel_units(e), "units")

    def _scroll_rail(self, d: int):
        self.rail.xview_scroll(d, "units")

    def _picks_n(self) -> int:
        n = self.settings.get("picks_n", 6)
        return n if n in self.PICK_N_CYCLE else 6

    def _cycle_picks_n(self):
        c = self.PICK_N_CYCLE
        n = c[(c.index(self._picks_n()) + 1) % len(c)]
        self.settings["picks_n"] = n
        self.save_settings()
        self.picks_n_btn.config(text=f"{n} 件")
        self._refresh_picks()

    def _next_picks(self):
        self._picks_seen |= {pk["movie"].get("title") for pk in self._picks}
        self._refresh_picks()

    def _paint_moods(self):
        counts = picks.mood_counts(list(self.now_showing.values())) if self.now_showing else {}
        for mood, b in self.mood_btns.items():
            n = counts.get(mood)
            on = mood == self._mood
            b.config(text=mood if n is None else f"{mood}  {n}", bg=C["accent"] if on else C["bg3"], fg=C["on_accent"] if on else C["text2"],
                     state="disabled" if n == 0 else "normal")

    def _set_mood(self, mood: str):
        if mood == self._mood:
            return
        self._mood = mood
        self._picks_seen = set()
        self._paint_moods()
        self._refresh_picks()
        self._filter_ns()            # MV-20: 上映中の表にも効く

    def _toggle_picks(self, force_hidden: bool | None = None):
        hidden = self.rail.winfo_manager() != "" if force_hidden is None else force_hidden
        if hidden:
            for w in (self.mood_row, self.picks_note, self.rail):
                w.pack_forget()
            self.picks_toggle.config(text="表示")
        else:
            self.mood_row.pack(fill="x", pady=(8, 0))
            self.picks_note.pack(fill="x", pady=(4, 0))
            self.rail.pack(fill="x", pady=(6, 0))
            self.picks_toggle.config(text="隠す")
        if force_hidden is None:
            self.settings["picks_hidden"] = hidden
            self.save_settings()

    def _refresh_picks(self):
        """札 n 枚 (3/6/9/12) を選び直して描く。写真と劇場は別スレッドで後から入る。"""
        for w in self.cards_frame.winfo_children():
            w.destroy()
        self._pick_cards = []
        self.rail.xview_moveto(0)
        if not self.now_showing:
            self.picks_note.config(text="上映中の一覧がまだありません")
            return
        today = datetime.now().strftime("%Y-%m-%d")
        self._paint_moods()
        movies, note = picks.mood_filter(list(self.now_showing.values()), self._mood)
        wl = picks.wl_showing_titles(self.watchlist, self.dir_watchlist, self.now_showing, self.theater_wl)
        dismissed = picks.load_dismissed(APP_DIR, today)
        n = self._picks_n()
        self._picks = picks.choose(movies, wl, self.taste, dismissed, n=n, exclude=self._picks_seen)
        if len(self._picks) < n and self._picks_seen:        # 候補が尽きた → 最初から
            self._picks_seen = set()
            self._picks = picks.choose(movies, wl, self.taste, dismissed, n=n)
            note = (note + "  ·  " if note else "") + "候補が尽きたので最初から"
        self.picks_note.config(text=note)
        for pk in self._picks:
            self._pick_cards.append(self._make_card(pk))
        threading.Thread(target=self._load_card_extras, args=(list(self._picks),), daemon=True).start()

    def _make_card(self, pk: dict) -> dict:
        """縦の札 (MV-20): 写真 184×104 | 題名 2 行 | 監督 · 理由 | 📍 館 | 観たい いまいち 観た | 印。幅は写真で決まる (200)。"""
        m = pk["movie"]
        card = tk.Frame(self.cards_frame, bg=C["card"], padx=8, pady=8)
        card.pack(side="left", padx=(0, 8))
        poster = tk.Label(card, image=self._blank_img, compound="center", text="…" if m.get("image") else "写真なし",
                          bg=C["bg3"], fg=C["text3"], font=self.F["caption"], cursor="hand2")
        poster.pack()
        title = tk.Label(card, text=m.get("title", ""), font=self.F["strong"], fg=C["text"], bg=C["card"], anchor="nw", justify="left",
                         wraplength=self.IMG_W, height=2, cursor="hand2")
        title.pack(fill="x", pady=(6, 0))
        sub = tk.Label(card, text=(m.get("director") or "監督 —") + "  ·  " + pk["reason"], font=self.F["caption"], fg=C["text2"], bg=C["card"],
                       anchor="nw", wraplength=self.IMG_W, justify="left", height=2)
        sub.pack(fill="x")
        theater = tk.Label(card, text="📍 …", font=self.F["caption"], fg=C["text3"], bg=C["card"], anchor="w")
        theater.pack(fill="x")
        row = tk.Frame(card, bg=C["card"])
        row.pack(fill="x", pady=(6, 0))
        mark = tk.Label(card, text="", font=self.F["caption"], fg=C["green"], bg=C["card"], anchor="w")
        mark.pack(fill="x", pady=(4, 0))

        def small(text, cmd, primary=False):
            return Button(row, text=text, font=self.F["caption"], bg=C["accent"] if primary else C["bg3"], fg=C["on_accent"] if primary else C["text2"],
                             activebackground=C["accent_hover"] if primary else C["border"], activeforeground=C["on_accent"] if primary else C["text"],
                             relief="flat", bd=0, padx=8, pady=2, cursor="hand2", command=cmd)

        small("観たい", lambda: self._pick_want(m, mark), primary=True).pack(side="left")
        small("いまいち", lambda: self._pick_meh(m)).pack(side="left", padx=(4, 0))
        small("観た", lambda: self._pick_seen(m, mark, card)).pack(side="left", padx=(4, 0))
        for w in (poster, title):
            w.bind("<Button-1>", lambda e, mv=m: self._theater_popup(mv.get("title", ""), mv.get("movie_id", "")) if mv.get("movie_id") else None)
        for w in (card, poster, title, sub, theater, row, mark):
            w.bind("<MouseWheel>", self._on_rail_wheel)
        return {"movie": m, "poster": poster, "theater": theater, "mark": mark}

    def _load_card_extras(self, pks: list[dict]):
        """別スレッド: 札の写真 (posters/ に残す。2 回目からは 0 要求) と最初の都道府県の 1 館 (1 日キャッシュ)。"""
        pref = (self.pref_codes or ["13"])[0]
        for pk in pks:
            m = pk["movie"]
            mid = str(m.get("movie_id") or "")
            path = None
            if m.get("image") and mid.isdigit():
                dest = POSTERS_DIR / f"{mid}.jpg"
                if fetch.fetch_poster(m["image"], dest) or dest.exists():     # 別の取り直しが先に書いていても使う
                    path = dest
            th = fetch.theaters_cached(mid, pref, APP_DIR) if mid.isdigit() else []
            name = th[0]["name"] if th else ""
            self.root.after(0, lambda mv=m, p_=path, nm=name: self._apply_card_extras(mv, p_, nm))

    def _apply_card_extras(self, m: dict, path, theater_name: str):
        for c in self._pick_cards:
            if c["movie"] is not m:
                continue
            c["theater"].config(text=f"📍 {theater_name}" if theater_name else "📍 —")
            shown = False
            if path and Image is not None:
                try:
                    img = Image.open(path).convert("RGB")
                    img.thumbnail((self.IMG_W, self.IMG_H))
                    canvas = Image.new("RGB", (self.IMG_W, self.IMG_H), C["bg3"])     # 札の高さを揃える
                    canvas.paste(img, ((self.IMG_W - img.width) // 2, (self.IMG_H - img.height) // 2))
                    ph = ImageTk.PhotoImage(canvas)
                    self._poster_imgs[m.get("movie_id")] = ph
                    c["poster"].config(image=ph, text="")
                    shown = True
                except Exception:  # noqa: BLE001 — 壊れた jpg 等
                    pass
            if not shown:
                c["poster"].config(text="写真なし")   # MV-17: 無い事が分かるように

    def _pick_want(self, m: dict, mark):
        title, director = m.get("title", ""), m.get("director", "")
        if any(w["title"] == title and w.get("director", "") == director for w in self.watchlist):
            mark.config(text="✅ 登録済み")
            return
        self.watchlist.append({"title": title, "director": director, "added_date": datetime.now().strftime("%Y-%m-%d"),
                               "status": "🟢 上映中", "matched_title": title, "movie_id": m.get("movie_id", "")})
        save_json(WATCHLIST_FILE, self.watchlist)
        self._refresh_wl()
        mark.config(text="✅ 観たい映画に")
        self._taste_feedback(m, "wl")

    def _pick_meh(self, m: dict):
        picks.add_dismissed(APP_DIR, datetime.now().strftime("%Y-%m-%d"), str(m.get("movie_id") or ""))
        self._taste_feedback(m, "skip")
        self._refresh_picks()

    def _pick_seen(self, m: dict, mark, card):
        ask = tk.Toplevel(self.root)
        ask.title("どうだった?")
        ask.configure(bg=C["card"])
        ask.transient(self.root)
        ask.resizable(False, False)
        tk.Label(ask, text=f"「{m.get('title', '')}」どうだった?", font=self.F["small"], fg=C["text"], bg=C["card"]).pack(padx=16, pady=(12, 6))
        bf = tk.Frame(ask, bg=C["card"])
        bf.pack(padx=16, pady=(0, 12))

        def answer(kind, text):
            self._taste_feedback(m, kind)
            mark.config(text=text)
            ask.destroy()

        Button(bf, text="👍 良かった", font=self.F["small"], bg=C["accent"], fg="white", relief="flat", bd=0, padx=10, pady=3, cursor="hand2", command=lambda: answer("good", "観た 👍")).pack(side="left")
        Button(bf, text="👎 いまいち", font=self.F["small"], bg=C["bg3"], fg=C["text2"], relief="flat", bd=0, padx=10, pady=3, cursor="hand2", command=lambda: answer("bad", "観た 👎")).pack(side="left", padx=(6, 0))
        Button(bf, text="閉じる", font=self.F["small"], bg=C["bg3"], fg=C["text2"], relief="flat", bd=0, padx=10, pady=3, cursor="hand2", command=ask.destroy).pack(side="left", padx=(6, 0))
        ask.update_idletasks()
        ask.geometry(f"+{card.winfo_rootx()}+{card.winfo_rooty() + card.winfo_height()}")

    # ---- 右: 上映中の一覧 (検索で絞る。監督名なら監督 WL / 映画 WL の未上映も並べる) ----
    def _filter_ns(self):
        self.ns_tree.delete(*self.ns_tree.get_children())
        self._ns_extra = {}
        raw = self.search_var.get()
        kw = ""
        # 仮の文字 (placeholder) が入っている間は検索しない。MV-7 の煙試験で見つけた: 『🔍 』付きの文字と比べていて、
        # 検索欄に触る前は一覧が空になっていた (書き起こしの源。v3.0 の exe が同じかは未確認)
        if raw and not getattr(getattr(self, "search_entry", None), "_has_ph", False):
            kw = normalize(raw)
        # 監督名の検索か
        is_dir = False
        if kw:
            for info in self.now_showing.values():
                d = info.get("director", "")
                if d and kw in normalize(d):
                    is_dir = True
                    break
            if not is_dir:
                for item in self.dir_watchlist:
                    if kw in normalize(item["name"]):
                        is_dir = True
                        break
            if not is_dir:
                for w in self.watchlist:
                    d = w.get("director", "")
                    if d and kw in normalize(d):
                        is_dir = True
                        break
        n = 0
        seen = set()
        self._ns_scores = {s["title"]: s for s in self.taste.score_all(list(self.now_showing.values()))}
        items = list(self.now_showing.items())
        mood_words = picks.MOODS.get(self._mood) or []
        if mood_words:                          # MV-20: 気分で表も絞る (0 件なら全部)
            kept, _ = picks.mood_filter([i for _, i in items], self._mood)
            keep_titles = {i.get("title") for i in kept}
            items = [(t, i) for t, i in items if t in keep_titles]
        if self._ns_sort == "taste" and self.taste.learned():
            items.sort(key=lambda kv: -self._ns_scores.get(kv[0], {}).get("score", 0.0))
        for title, info in items:
            director = info.get("director", "")
            if kw:
                if kw not in normalize(title) and kw not in normalize(director):
                    continue
            n += 1
            marker = "🟢" if (is_dir and kw) else str(n)
            star = self._ns_scores.get(title, {}).get("stars", "")
            self.ns_tree.insert("", "end", iid=title, values=(marker, star, title, director))
            seen.add(normalize(title))
        extra = 0
        if is_dir and kw:
            for item in self.dir_watchlist:
                if kw not in normalize(item["name"]):
                    continue
                for m in item.get("movies", []):
                    t = m["title"]
                    if normalize(t) in seen:
                        continue
                    seen.add(normalize(t))
                    iid = f"__ex__{item['name']}\t{t}"
                    self.ns_tree.insert("", "end", iid=iid, values=("⬜", "", t, item["name"]))
                    self._ns_extra[iid] = {"title": t, "movie_id": m.get("movie_id", ""), "url": m.get("url", ""), "director": item["name"]}
                    extra += 1
            for w in self.watchlist:
                d = w.get("director", "")
                if not d or kw not in normalize(d):
                    continue
                t = w["title"]
                if normalize(t) in seen:
                    continue
                seen.add(normalize(t))
                iid = f"__ex__wl\t{t}"
                self.ns_tree.insert("", "end", iid=iid, values=("⬜", "", t, d))
                self._ns_extra[iid] = {"title": t, "movie_id": w.get("movie_id", ""), "url": w.get("url", ""), "director": d}
                extra += 1
        total = len(self.now_showing)
        head = f"{self._mood} " if mood_words else ""
        if kw and is_dir:
            self.ns_cnt.config(text=f"{head}🟢{n} ⬜{extra} / {total}件")
        elif kw or mood_words:
            self.ns_cnt.config(text=f"{head}{n}/{total}件")
        else:
            self.ns_cnt.config(text=f"{total}件")

    # ---- 左: 映画のウォッチリスト ----
    def _refresh_wl(self):
        self.wl_tree.delete(*self.wl_tree.get_children())
        if not self.watchlist:   # 空の画面には次の一手 (HIG writing)
            self.wl_tree.insert("", "end", values=("", "観たい映画を追加すると、上映中かどうかをここで知らせます", "", ""))
        for item in self.watchlist:
            st = item.get("status", "⏳ 未チェック")
            director = item.get("director", "")
            info = "🎬 劇場を検索 →" if "上映中" in st else ("作品ページ →" if st.startswith("📅") else item.get("added_date", ""))
            self.wl_tree.insert("", "end", values=(st, item["title"], director, info))
        if self._left_mode == "movie":
            self.wl_cnt.config(text=f"{len(self.watchlist)}件")

    # ---- 左: 監督のウォッチリスト ----
    def _on_dir_wl_entry_selected(self, name: str):
        self.dir_wl_entry.delete(0, "end")
        self.dir_wl_entry.insert(0, name)

    def _update_dir_wl_combo(self):
        names = ["── すべて ──"] + [d["name"] for d in self.dir_watchlist]
        self.dir_wl_combo["values"] = names

    def _refresh_dir_wl(self):
        self.dir_wl_tree.delete(*self.dir_wl_tree.get_children())
        self._update_dir_wl_combo()
        filt = self._dir_wl_filter_var.get()
        show_all = filt.startswith("─")
        total = 0
        showing = 0
        rows = []
        for item in self.dir_watchlist:
            name = item["name"]
            if not show_all and name != filt:
                continue
            movies = item.get("movies", [])
            for m in movies:
                title = m["title"]
                mid = m.get("movie_id", "")
                is_ns = title in self.now_showing
                if not is_ns and mid:
                    is_ns = any(info.get("movie_id") == mid for info in self.now_showing.values())
                st = "🟢 上映中" if is_ns else "⬜ 未上映"
                rows.append((0 if is_ns else 1, title, st, name, m))
                total += 1
                if is_ns:
                    showing += 1
        rows.sort(key=lambda r: (r[0], r[3], r[1]))
        for _, title, st, dname, m in rows:
            iid = f"{dname}\t{title}"
            self.dir_wl_tree.insert("", "end", iid=iid, values=(st, title, dname))
        if total:
            self._dir_wl_status.config(text=f"{total}作品 (上映中: {showing})",
                                       fg=C["green"] if showing > 0 else C["text3"])
        elif self.dir_watchlist:
            has_empty = any(not item.get("movies") for item in self.dir_watchlist)
            if has_empty:
                self._dir_wl_status.config(text="📡 作品を取得中...", fg=C["accent"])
            else:
                self._dir_wl_status.config(text="↑ 絞り込みを変更してください", fg=C["text3"])
        else:
            self._dir_wl_status.config(text="☝ 監督名を入力して追加", fg=C["text3"])
        if self._left_mode == "director":
            self.wl_cnt.config(text=f"{len(self.dir_watchlist)}人")

    def _get_dir_wl_movie_info(self, iid: str) -> tuple[str, str, dict]:
        parts = iid.split("\t", 1)
        if len(parts) != 2:
            return "", "", {}
        dname, title = parts
        for item in self.dir_watchlist:
            if item["name"] == dname:
                for m in item.get("movies", []):
                    if m["title"] == title:
                        return dname, title, m
        return dname, title, {}

    def _add_dir_to_wl(self):
        raw = self.dir_wl_entry.get().strip()
        if not raw or raw == getattr(self.dir_wl_entry, "_ph", ""):
            messagebox.showinfo("確認", "監督名を入力してください。")
            return
        nq = normalize(raw)
        if any(normalize(d["name"]) == nq for d in self.dir_watchlist):
            messagebox.showinfo("確認", f"「{raw}」は既に監督ウォッチリストにあります。")
            return
        # 手元にある正式な表記に寄せる
        formal = None
        for info in self.now_showing.values():
            d = info.get("director", "")
            if d and normalize(d) == nq:
                formal = d
                break
        if not formal:
            for w in self.watchlist:
                d = w.get("director", "")
                if d and normalize(d) == nq:
                    formal = d
                    break
        if not formal and not self.dir_wl_entry.is_from_suggestion:
            if messagebox.askyesno("確認", f"「{raw}」をeiga.comで検索しますか？\n推薦リストから選ぶと確実に登録できます。"):
                self._status("eiga.comで検索中...")

                def _verify_and_add():
                    pid = fetch_person_id(raw)
                    self.root.after(0, lambda: self._on_director_verified(raw, pid))

                threading.Thread(target=_verify_and_add, daemon=True).start()
            return
        if not formal:
            formal = raw
        new_item = {"name": formal, "added_date": datetime.now().strftime("%Y-%m-%d"), "person_id": "", "movies": []}
        self.dir_watchlist.append(new_item)
        save_json(DIR_WL_FILE, self.dir_watchlist)
        self.dir_wl_entry.delete(0, "end")
        self.dir_wl_entry._has_ph = False
        self.dir_wl_entry.event_generate("<FocusOut>")
        self._refresh_dir_wl()
        self._status(f"✅ 監督「{formal}」を追加しました")
        self._dir_wl_status.config(text="📡 作品を取得中...", fg=C["accent"])
        threading.Thread(target=self._fetch_dir_filmography, args=(formal,), daemon=True).start()

    def _on_director_verified(self, raw_name: str, person_id: str):
        self._status("")
        if not person_id:
            messagebox.showwarning("該当なし", f"「{raw_name}」はeiga.comに見つかりませんでした。\n正確な名前で再度お試しください。")
            return
        nq = normalize(raw_name)
        if any(normalize(d["name"]) == nq for d in self.dir_watchlist):
            messagebox.showinfo("確認", f"「{raw_name}」は既に監督ウォッチリストにあります。")
            return
        new_item = {"name": raw_name, "added_date": datetime.now().strftime("%Y-%m-%d"), "person_id": person_id, "movies": []}
        self.dir_watchlist.append(new_item)
        save_json(DIR_WL_FILE, self.dir_watchlist)
        self.dir_wl_entry.delete(0, "end")
        self.dir_wl_entry._has_ph = False
        self.dir_wl_entry.event_generate("<FocusOut>")
        self._refresh_dir_wl()
        self._status(f"✅ 監督「{raw_name}」を追加しました")
        self._dir_wl_status.config(text="作品を取得中...", fg=C["accent"])
        threading.Thread(target=self._fetch_dir_filmography, args=(raw_name,), daemon=True).start()

    def _fetch_dir_filmography(self, director_name: str):
        """別スレッド: 人物ページ → 無ければ検索 (作品ページの監督で確かめる) → 上映中からも拾う。"""
        nq = normalize(director_name)
        found_movies = []
        person_id = ""
        for item in self.dir_watchlist:
            if item["name"] == director_name:
                person_id = item.get("person_id", "")
                break
        if not person_id:
            try:
                person_id = fetch_person_id(director_name)
            except Exception:
                pass
        if person_id:
            try:
                found_movies = fetch_directed_movies(person_id)
            except Exception:
                pass
        if not found_movies:
            try:
                hits = search_eiga(director_name, max_pages=4)
                seen = set()
                for hit in hits:
                    if hit["title"] in seen:
                        continue
                    mid = hit.get("movie_id", "")
                    if mid:
                        try:
                            d = fetch_movie_director(mid)
                            if d and nq in normalize(d):
                                found_movies.append({"title": hit["title"], "movie_id": mid, "url": hit.get("url", "")})
                                seen.add(hit["title"])
                        except Exception:
                            continue
            except Exception:
                pass
        seen_titles = {m["title"] for m in found_movies}
        for title, info in self.now_showing.items():
            d = info.get("director", "")
            if nq in normalize(d) and title not in seen_titles:
                found_movies.append({"title": title, "movie_id": info.get("movie_id", ""), "url": info.get("url", "")})

        def update():
            for item in self.dir_watchlist:
                if item["name"] == director_name:
                    item["movies"] = found_movies
                    if person_id:
                        item["person_id"] = person_id
                    break
            save_json(DIR_WL_FILE, self.dir_watchlist)
            self._refresh_dir_wl()
            cnt = len(found_movies)
            src = "人物ページ" if person_id else "検索"
            self._status(f"✅ 「{director_name}」の監督作品 {cnt}件を取得 ({src})")

        self.root.after(0, update)

    def _refetch_dir_filmography(self):
        filt = self._dir_wl_filter_var.get()
        targets = []
        if not filt.startswith("─"):
            targets = [filt]
        else:
            sel = self.dir_wl_tree.selection()
            if sel:
                vals = self.dir_wl_tree.item(sel[0], "values")
                if len(vals) >= 3:
                    targets = [vals[2]]
        if not targets:
            targets = [d["name"] for d in self.dir_watchlist]
        if not targets:
            messagebox.showinfo("確認", "監督ウォッチリストが空です。")
            return
        self._dir_wl_status.config(text=f"📡 {len(targets)}人の作品を再取得中...", fg=C["accent"])
        self._status(f"📡 フィルモグラフィー再取得中... ({', '.join(targets[:3])})")

        def work():
            for name in targets:
                self._fetch_dir_filmography(name)

        threading.Thread(target=work, daemon=True).start()

    def _remove_dir_from_wl(self):
        filt = self._dir_wl_filter_var.get()
        target = None
        if not filt.startswith("─"):
            target = filt
        else:
            sel = self.dir_wl_tree.selection()
            if sel:
                vals = self.dir_wl_tree.item(sel[0], "values")
                if len(vals) >= 3:
                    target = vals[2]
        if not target:
            messagebox.showinfo("確認", "削除する監督をフィルターで選択するか、\nリストから作品を選択してください。")
            return
        if not messagebox.askyesno("削除確認", f"監督「{target}」とその全作品を\nウォッチリストから削除しますか？"):
            return
        self.dir_watchlist = [d for d in self.dir_watchlist if d["name"] != target]
        save_json(DIR_WL_FILE, self.dir_watchlist)
        if not filt.startswith("─"):
            self._dir_wl_filter_var.set("── すべて ──")
        self._refresh_dir_wl()
        self._status(f"🗑 監督「{target}」を削除")

    def _check_dir_wl_status(self):
        if not self.dir_watchlist:
            messagebox.showinfo("確認", "監督ウォッチリストが空です。")
            return
        if not self.now_showing:
            self._status("📡 上映情報を先に取得します...")
            self._show_progress(True)
            self.is_fetching = True

            def work():
                def prog(p, t):
                    self.root.after(0, lambda: self._on_prog(p, t))

                ms = fetch_now_showing(progress_cb=prog)
                d = _merge_ranking(ms, fetch_ranking())
                self._coming = fetch.coming_cached(APP_DIR)   # MV-22
                self.root.after(0, lambda: self._on_fetch_then_dir_check(d))

            threading.Thread(target=work, daemon=True).start()
            return
        self._do_dir_wl_check()

    def _on_fetch_then_dir_check(self, movies):
        self.now_showing = movies
        self.is_fetching = False
        self._show_progress(False)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save_cache(ts)
        self._on_data_ready(ts=ts)
        self._do_dir_wl_check()

    def _do_dir_wl_check(self):
        lines = []
        for item in self.dir_watchlist:
            name = item["name"]
            showing_titles = []
            for m in item.get("movies", []):
                title = m["title"]
                mid = m.get("movie_id", "")
                is_ns = title in self.now_showing
                if not is_ns and mid:
                    is_ns = any(info.get("movie_id") == mid for info in self.now_showing.values())
                if is_ns:
                    showing_titles.append(title)
            total = len(item.get("movies", []))
            if showing_titles:
                lines.append(f"🟢 {name} ({len(showing_titles)}/{total}本上映中)")
                for t in showing_titles[:5]:
                    lines.append(f"    • {t}")
                if len(showing_titles) > 5:
                    lines.append(f"    … 他{len(showing_titles) - 5}本")
            else:
                lines.append(f"⚪ {name} ({total}作品) — 上映中作品なし")
        self._refresh_dir_wl()
        with_showing = sum(
            1 for item in self.dir_watchlist
            if any(m["title"] in self.now_showing for m in item.get("movies", []))
        )
        messagebox.showinfo(
            "監督 上映チェック結果",
            f"🎥 {with_showing} / {len(self.dir_watchlist)} 人の監督に上映中作品あり\n\n" + "\n".join(lines),
        )

    def _on_dir_wl_dblclick(self, event):
        sel = self.dir_wl_tree.selection()
        if not sel:
            return
        dname, title, m = self._get_dir_wl_movie_info(sel[0])
        if not m:
            return
        mid = m.get("movie_id", "")
        is_ns = title in self.now_showing
        if not is_ns and mid:
            is_ns = any(info.get("movie_id") == mid for info in self.now_showing.values())
        if is_ns and mid:
            self._theater_popup(title, mid)
            return
        if mid:
            webbrowser.open(f"{EIGA_BASE}/movie/{mid}/")
            return
        if m.get("url"):
            webbrowser.open(m["url"])

    def _theater_for_dir_wl(self):
        sel = self.dir_wl_tree.selection()
        if not sel:
            messagebox.showinfo("確認", "映画を選択してください。")
            return
        dname, title, m = self._get_dir_wl_movie_info(sel[0])
        if not m:
            return
        mid = m.get("movie_id", "")
        is_ns = title in self.now_showing
        if not is_ns and mid:
            is_ns = any(info.get("movie_id") == mid for info in self.now_showing.values())
        if is_ns and mid:
            self._theater_popup(title, mid)
            return
        messagebox.showinfo("確認", "上映中の映画のみ劇場を探せます。")

    # ---- 映画の追加 ----
    def _add_movie(self):
        raw_title = self.add_entry.get().strip()
        if not raw_title or raw_title == getattr(self.add_entry, "_ph", ""):
            messagebox.showinfo("確認", "映画タイトルを入力してください。")
            return
        # 1. 上映中から
        matched_title = find_match(raw_title, self.now_showing) if self.now_showing else None
        if matched_title:
            info = self.now_showing[matched_title]
            self._do_add_to_watchlist(matched_title, info)
            return
        # 2. 検索の写しから
        cache_match = find_match(raw_title, self._eiga_search_cache) if self._eiga_search_cache else None
        if cache_match:
            info = self._eiga_search_cache[cache_match]
            self._do_add_to_watchlist(cache_match, info)
            return
        # 3. eiga.com で検索 (推薦から選んだ物は聞かずに)
        if not self.add_entry.is_from_suggestion:
            if messagebox.askyesno("確認", f"「{raw_title}」をeiga.comで検索しますか？\n推薦リストから選ぶと確実に登録できます。"):
                self._status("eiga.comで検索中...")
                self.root.update_idletasks()

                def work():
                    results = search_eiga(raw_title)
                    self.root.after(0, lambda: self._on_search_for_add(raw_title, results))

                threading.Thread(target=work, daemon=True).start()
            return
        self._status("eiga.comで検索中...")
        self.root.update_idletasks()

        def work():
            results = search_eiga(raw_title)
            self.root.after(0, lambda: self._on_search_for_add(raw_title, results))

        threading.Thread(target=work, daemon=True).start()

    def _on_search_for_add(self, raw_title: str, search_results: list[dict]):
        if not search_results:
            all_titles = list(self.now_showing.keys())
            suggestions = ranked_suggestions(raw_title, all_titles, threshold=0.1, max_items=5, alpha=2.5)
            msg = f"「{raw_title}」はeiga.comに見つかりませんでした。"
            if suggestions:
                hint = "\n".join(f"  • {name}" for name, _ in suggestions)
                msg += f"\n\nもしかして:\n{hint}"
            messagebox.showwarning("該当なし", msg)
            self._status("")
            return
        for r in search_results:
            if r["title"] not in self._eiga_search_cache:
                self._eiga_search_cache[r["title"]] = r
        search_dict = {r["title"]: r for r in search_results}
        matched = find_match(raw_title, search_dict)
        if matched:
            info = search_dict[matched]
            self._do_add_to_watchlist(matched, info)
            return
        if len(search_results) == 1:
            only = search_results[0]
            if messagebox.askyesno("確認", f"「{raw_title}」→「{only['title']}」を追加しますか？"):
                self._do_add_to_watchlist(only["title"], only)
            self._status("")
            return
        titles = [r["title"] for r in search_results]
        suggestions = ranked_suggestions(raw_title, titles, threshold=0.05, max_items=5, alpha=2.0)
        if not suggestions:
            suggestions = [(t, 0) for t in titles[:5]]
        hint = "\n".join(f"  • {name}" for name, _ in suggestions)
        msg = f"「{raw_title}」の検索結果:\n\n{hint}\n\n推薦から選んで入力し直してください。"
        messagebox.showinfo("eiga.com 検索結果", msg)
        self._status("")

    def _do_add_to_watchlist(self, title: str, info: dict):
        if any(w["title"] == title for w in self.watchlist):
            messagebox.showinfo("確認", f"「{title}」は既にウォッチリストに登録済みです。")
            return
        director = info.get("director", "")
        movie_id = info.get("movie_id", "")
        if not director and movie_id:
            director = fetch_movie_director(movie_id)
            info["director"] = director
        is_showing = title in self.now_showing
        status = "🟢 上映中" if is_showing else "⬜ 未上映"
        self.watchlist.append({
            "title": title,
            "director": director,
            "added_date": datetime.now().strftime("%Y-%m-%d"),
            "status": status,
            "matched_title": title if is_showing else "",
            "movie_id": movie_id,
            "url": info.get("url", ""),
        })
        save_json(WATCHLIST_FILE, self.watchlist)
        self._taste_feedback({"title": title, "director": director}, "wl")
        self._refresh_wl()
        self._clear_input()
        disp = f"「{title}」"
        if director:
            disp += f"（{director}）"
        mark = "上映中！" if is_showing else ""
        self._status(f"✅ {disp} を追加 {mark}")

    def _clear_input(self):
        for entry in (self.add_entry, self.dir_entry):
            entry.delete(0, "end")
            entry._has_ph = False
            entry.event_generate("<FocusOut>")

    def _remove_movie(self):
        sel = self.wl_tree.selection()
        if not sel:
            messagebox.showinfo("確認", "削除する映画を選択してください。")
            return
        vals = self.wl_tree.item(sel[0], "values")
        title = vals[1]
        director = vals[2] if len(vals) > 2 else ""
        disp = f"「{title}」" + (f"（{director}）" if director else "")
        if not messagebox.askyesno("削除確認", f"{disp}をウォッチリストから削除しますか？"):
            return
        self.watchlist = [w for w in self.watchlist if not (w["title"] == title and w.get("director", "") == director)]
        save_json(WATCHLIST_FILE, self.watchlist)
        self._refresh_wl()
        self._status(f"🗑 {disp}を削除")

    def _add_from_ns(self):
        sel = self.ns_tree.selection()
        if not sel:
            messagebox.showinfo("確認", "右の上映中リストから追加したい映画を選択してください。")
            return
        title, info, is_ns = self._get_ns_item_info(sel[0])
        director = info.get("director", "")
        if any(w["title"] == title and w.get("director", "") == director for w in self.watchlist):
            messagebox.showinfo("確認", f"「{title}」は既にウォッチリストに登録済みです。")
            return
        if is_ns:
            self.watchlist.append({
                "title": title, "director": director,
                "added_date": datetime.now().strftime("%Y-%m-%d"),
                "status": "🟢 上映中", "matched_title": title,
                "movie_id": info.get("movie_id", ""),
            })
        else:
            self.watchlist.append({
                "title": title, "director": director,
                "added_date": datetime.now().strftime("%Y-%m-%d"),
                "status": "⬜ 未上映",
                "movie_id": info.get("movie_id", ""), "url": info.get("url", ""),
            })
        save_json(WATCHLIST_FILE, self.watchlist)
        self._refresh_wl()
        self._status(f"追加: 「{title}」")
        self._taste_feedback({"title": title, "director": director}, "wl")

    # ---- 上映チェック (映画 WL) ----
    def _check_status(self):
        if not self.watchlist:
            messagebox.showinfo("確認", "ウォッチリストが空です。\n映画を追加してください。")
            return
        if not self.now_showing:
            self._status("📡 上映情報を先に取得します...")
            self._show_progress(True)
            self.is_fetching = True

            def work():
                def prog(p, t):
                    self.root.after(0, lambda: self._on_prog(p, t))

                ms = fetch_now_showing(progress_cb=prog)
                d = _merge_ranking(ms, fetch_ranking())
                self._coming = fetch.coming_cached(APP_DIR)   # MV-22
                self.root.after(0, lambda: self._on_fetch_then_check(d))

            threading.Thread(target=work, daemon=True).start()
            return
        self._do_check(silent=False)

    def _on_fetch_then_check(self, movies):
        if self._fetch_got_nothing(movies):
            messagebox.showwarning("上映チェック", f"上映情報を取得できませんでした。\n{self._fetch_failed_text()}")
            return
        self.now_showing = movies
        self.is_fetching = False
        self._show_progress(False)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save_cache(ts)
        self._on_data_ready(ts=ts)
        self._do_check(silent=False)

    def _do_check(self, silent=False):
        showing = 0
        for item in self.watchlist:
            mt = find_match(item["title"], self.now_showing)
            if mt:
                item["status"] = "🟢 上映中"
                item["matched_title"] = mt
                info = self.now_showing.get(mt, {})
                item["movie_id"] = info.get("movie_id", "")
                item["url"] = info.get("url", "")
                if not item.get("director") and info.get("director"):
                    item["director"] = info["director"]
                showing += 1
            else:
                if item.get("status") != "🟢 上映中":
                    if item.get("matched_title"):
                        item["status"] = "⚪ 上映終了"
                    elif item.get("movie_id"):
                        item["status"] = "⬜ 未上映"
                    else:
                        item["status"] = "⚪ 未確認"
                else:
                    item["status"] = "⚪ 上映終了"
                item.pop("matched_title", None)
                item.pop("release", None)
        # 公開予定 (MV-22): 上映中でない物に『📅 11/14 公開』。取れていなければ何も付けない
        today = datetime.now().strftime("%Y-%m-%d")
        for c in notify.coming_items(self.watchlist, self._coming, today):
            for item in self.watchlist:
                if item.get("title") == c["title"] and "上映中" not in item.get("status", ""):
                    item["status"] = "📅 " + notify.release_label(c["release"], c["stream"])
                    item["release"] = c["release"]
                    if not item.get("movie_id") and c.get("movie_id"):
                        item["movie_id"], item["url"] = c["movie_id"], c.get("url", "")
        save_json(WATCHLIST_FILE, self.watchlist)
        self._refresh_wl()
        self._status(f"✅ {showing}/{len(self.watchlist)}件が上映中")
        if not silent:
            lines = []
            for item in self.watchlist:
                s = item.get("status", "")
                t = item["title"]
                d = item.get("director", "")
                label = f"{t}（{d}）" if d else t
                if "上映中" in s:
                    mt = item.get("matched_title", "")
                    line = f"🟢 {label}"
                    if mt and mt != t:
                        line += f"\n     → {mt}"
                else:
                    line = f"⚪ {label}"
                lines.append(line)
            messagebox.showinfo("上映チェック結果", f"🎬 {showing} / {len(self.watchlist)} 件が上映中\n\n" + "\n".join(lines))

    def _on_wl_dblclick(self, event):
        sel = self.wl_tree.selection()
        if not sel:
            return
        vals = self.wl_tree.item(sel[0], "values")
        status, title = vals[0], vals[1]
        item = next((w for w in self.watchlist if w["title"] == title), None)
        if "上映中" in status and item and item.get("movie_id"):
            self._theater_popup(title, item["movie_id"])
            return
        if item:
            url = item.get("url", "")
            if not url and item.get("movie_id"):
                url = f"{EIGA_BASE}/movie/{item['movie_id']}/"
            if url:
                webbrowser.open(url)
                return
        messagebox.showinfo("情報", f"「{title}」は現在上映が確認されていません。\n\n「🔄 上映チェック」で最新情報を確認してください。")

    # ---- 右の一覧の行 ----
    def _get_ns_item_info(self, iid: str) -> tuple[str, dict, bool]:
        if iid in self.now_showing:
            return iid, self.now_showing[iid], True
        ex = self._ns_extra.get(iid, {})
        if ex:
            return ex.get("title", iid), ex, False
        return iid, {}, False

    def _on_ns_dblclick(self, event):
        sel = self.ns_tree.selection()
        if not sel:
            return
        title, info, is_ns = self._get_ns_item_info(sel[0])
        mid = info.get("movie_id", "")
        if is_ns and mid:
            self._theater_popup(title, mid)
            return
        if mid:
            webbrowser.open(f"{EIGA_BASE}/movie/{mid}/")
            return
        if info.get("url"):
            webbrowser.open(info["url"])

    def _theater_for_ns(self):
        sel = self.ns_tree.selection()
        if not sel:
            messagebox.showinfo("確認", "映画を選択してください。")
            return
        title, info, is_ns = self._get_ns_item_info(sel[0])
        mid = info.get("movie_id", "")
        if is_ns and mid:
            self._theater_popup(title, mid)
            return
        if not is_ns:
            messagebox.showinfo("確認", f"「{title}」は現在上映していません。")

    def _get_all_director_candidates(self) -> list[str]:
        dirs = set()
        for info in self.now_showing.values():
            d = info.get("director", "")
            if d:
                for name in d.split(", "):
                    name = name.strip()
                    if name:
                        dirs.add(name)
        for item in self.watchlist:
            d = item.get("director", "")
            if d:
                dirs.add(d)
        for dw in self.dir_watchlist:
            dirs.add(dw["name"])
        return sorted(dirs)

    def _search_eiga_directors(self, query: str) -> list[str]:
        """eiga.com の検索から人物名を 10 件まで (別スレッドから)。"""
        return search_persons(query)

    # ---- 劇場のポップアップ ----
    def _theater_popup(self, title: str, movie_id: str):
        pref_codes = self.pref_codes
        pref_names = [PREFECTURES.get(c, c) for c in pref_codes]

        popup = tk.Toplevel(self.root)
        popup.title(f"劇場: {title}")
        popup.geometry("600x580")
        popup.configure(bg=C["bg"])
        popup.transient(self.root)
        popup.grab_set()

        h = tk.Frame(popup, bg=C["card"], pady=14)
        h.pack(fill="x")
        tk.Label(h, text=title, font=self.F["h2"], fg=C["text"], bg=C["card"], wraplength=550, justify="left").pack(padx=20, anchor="w")
        tk.Label(h, text=", ".join(pref_names), font=self.F["caption"], fg=C["text2"], bg=C["card"]).pack(padx=20, anchor="w", pady=(2, 0))
        tk.Frame(popup, bg=C["separator"], height=1).pack(fill="x")

        loading = tk.Label(popup, text="映画館情報を取得中...", font=self.F["normal"], fg=C["text2"], bg=C["bg"])
        loading.pack(pady=30)

        container = tk.Frame(popup, bg=C["bg"])
        self._popup_canvas = None

        def load_theaters():
            def work():
                result = fetch_theaters_multi(movie_id, pref_codes, APP_DIR)
                popup.after(0, lambda: show(result))

            threading.Thread(target=work, daemon=True).start()

        def show(result: dict[str, list[dict]]):
            loading.destroy()
            container.pack(fill="both", expand=True, padx=20, pady=8)
            total = sum(len(v) for v in result.values())
            if not total:
                tk.Label(container, text="選択地域では上映館が見つかりませんでした。\n\n「変更」で別の地域を追加するか、eiga.comで確認してください。",
                         font=self.F["normal"], fg=C["text2"], bg=C["bg"], wraplength=520, justify="left").pack(pady=24)
                Button(container, text="eiga.com で劇場を探す", font=self.F["btn"], bg=C["accent"], fg="white", relief="flat",
                          cursor="hand2", bd=0, padx=16, pady=6,
                          command=lambda: webbrowser.open(f"{EIGA_BASE}/movie/{movie_id}/theater/")).pack(pady=8)
                return
            tk.Label(container, text=f"全 {total}館で上映中", font=self.F["h2"], fg=C["green"], bg=C["bg"]).pack(anchor="w", pady=(0, 8))
            canvas = tk.Canvas(container, bg=C["bg"], highlightthickness=0)
            self._popup_canvas = canvas
            sb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
            sf = tk.Frame(canvas, bg=C["bg"])
            sf.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=sf, anchor="nw")
            canvas.configure(yscrollcommand=sb.set)

            def mwheel(e):
                canvas.yview_scroll(wheel_units(e), "units")

            canvas.bind_all("<MouseWheel>", mwheel)
            idx = 0
            for region_name, theaters in result.items():
                rh = tk.Frame(sf, bg=C["bg"], pady=6, padx=4)
                rh.pack(fill="x", pady=(12, 4))
                tk.Label(rh, text=f"{region_name}  ({len(theaters)}館)", font=self.F["h2"], fg=C["text"], bg=C["bg"]).pack(anchor="w")
                tk.Frame(sf, bg=C["separator"], height=1).pack(fill="x", padx=4)
                for th in theaters:
                    row_bg = C["card"] if idx % 2 == 0 else C["card_alt"]
                    row = tk.Frame(sf, bg=row_bg, pady=8, padx=14)
                    row.pack(fill="x", pady=1)
                    tk.Label(row, text=th["name"], font=self.F["normal"], fg=C["text"], bg=row_bg, anchor="w").pack(fill="x")
                    lnk = tk.Label(row, text="上映スケジュールを見る →", font=self.F["small"], fg=C["accent"], bg=row_bg, cursor="hand2")
                    lnk.pack(anchor="w", pady=(2, 0))
                    u = th["url"]
                    lnk.bind("<Button-1>", lambda e, url=u: webbrowser.open(url))
                    lnk.bind("<Enter>", lambda e, l=lnk: l.config(fg=C["accent_hover"]))
                    lnk.bind("<Leave>", lambda e, l=lnk: l.config(fg=C["accent"]))
                    idx += 1
            canvas.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")

        tk.Frame(popup, bg=C["separator"], height=1).pack(fill="x")
        bf = tk.Frame(popup, bg=C["bg"])
        bf.pack(fill="x", padx=20, pady=10)
        Button(bf, text="eiga.com で全地域を見る", font=self.F["btn"], bg=C["bg3"], fg=C["text2"], relief="flat", cursor="hand2",
                  bd=0, padx=14, pady=6,
                  command=lambda: webbrowser.open(f"{EIGA_BASE}/movie/{movie_id}/theater/")).pack(side="right")

        def cleanup(_=None):
            if self._popup_canvas:
                try:
                    self._popup_canvas.unbind_all("<MouseWheel>")
                except Exception:
                    pass

        popup.bind("<Destroy>", cleanup)
        load_theaters()


def send_mail_cli() -> int:
    """画面無しで 取得 → 判定 → 送信 → 終了 (タスク スケジューラから)。戻りは終了コード。
    exe (console=False) では stdout が無いので print は捨てる (記録は notify_log.json)。落ちても窓を出さない。"""
    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        return _send_mail_cli()
    except Exception as e:  # 窓無しの exe で例外の窓を出して止まらないように
        _say(f"失敗: {type(e).__name__}: {e}")
        try:
            log_p = APP_DIR / "notify_log.json"
            log = load_json(log_p)
            log = log if isinstance(log, list) else []
            log.append({"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "kind": "cli", "count": 0, "ok": False, "error": f"{type(e).__name__}: {str(e)[:80]}", "why": ""})
            save_json(log_p, log[-200:])
        except Exception:
            pass
        return 3


def _say(msg: str) -> None:
    if sys.stdout is not None:
        print(msg)


def _send_mail_cli() -> int:
    settings = load_json(SETTINGS_FILE) or {}
    wl = load_json(WATCHLIST_FILE)
    dwl = load_json(DIR_WL_FILE)
    ms = fetch_now_showing()
    now_showing = _merge_ranking(ms, fetch_ranking())
    coming = fetch.coming_cached(APP_DIR)                                   # MV-22 (1 日キャッシュ)
    if not now_showing:
        _say("取得できませんでした: " + (fetch.error_text(200) or "0 件"))
        return 2
    save_json(CACHE_FILE, {"updated": datetime.now().strftime("%Y-%m-%d %H:%M"), "movies": list(now_showing.values())})
    thw = load_json(THEATER_WL_FILE)
    thw = thw if isinstance(thw, list) else []
    for t in thw:                                   # 館の作品 (MV-19)。取れなければ前回のまま
        movies = fetch.theater_movies_cached(t.get("url", ""), APP_DIR)
        if movies:
            t["movies"], t["updated"] = movies, datetime.now().strftime("%Y-%m-%d %H:%M")
    if thw:
        save_json(THEATER_WL_FILE, thw)
    r = notify.run(settings, APP_DIR, wl, dwl, now_showing, kind="auto",
                   theaters_fn=lambda mid, prefs: fetch_theaters_multi(mid, prefs, APP_DIR),
                   picks_fn=lambda: MovieApp._picks_for_mail(None, now_showing, wl, dwl, thw), theater_wl=thw,
                   coming=notify.coming_items(wl, coming, datetime.now().strftime("%Y-%m-%d")))
    if r.get("error"):
        _say("メール: " + r["error"])
        return 1
    _say("メール: " + (f"送りました ({r['count']} 件)" if r.get("sent") else f"送っていません ({r.get('why', '')})"))
    return 0


def main():
    if "--send-mail" in sys.argv[1:]:
        sys.exit(send_mail_cli())
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    MovieApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
