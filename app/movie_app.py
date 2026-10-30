"""
🎬 映画上映チェッカー v3.0
eiga.com から現在上映中の映画を取得し、ウォッチリストと照合するデスクトップアプリ

機能:
  - 起動時に上映情報を自動取得（キャッシュが6時間以内なら再利用）
  - バックグラウンドで6時間ごとに自動更新
  - ウォッチリストの映画が上映中か一目でわかる
  - 上映中の映画をダブルクリック → 地域の映画館ポップアップ
  - 複数都道府県で地域を絞り込み
  - 上映中リストからワンクリックでウォッチリスト追加
  - 監督名を表示・オートコンプリートで入力

(この源は 映画チェッカー.exe (PyInstaller、Python 3.10) の bytecode から
 2026-10-30 に書き起こした物。元の .py は見つからなかった。行の並びと名前は
 bytecode のとおり、コメントと docstring は書き起こしの際に足した)
"""
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from moviecheck import fetch, notify, store
from mail_dialog import MailSettingsDialog
from moviecheck.fetch import (  # noqa: F401 — 画面が使う名前 (置き場だけ変えた)
    EIGA_BASE, PREFECTURES,
    fetch_now_showing, fetch_ranking, fetch_theaters_multi, search_eiga,
    fetch_movie_director, fetch_person_id, fetch_directed_movies, search_persons,
)
from moviecheck.match import normalize, find_match, suggest_score, ranked_suggestions  # noqa: F401
from moviecheck.store import load_json, save_json

# ---- 置き場 (exe なら exe の隣、源なら .py の隣) ----
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
    _BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).parent
    _BUNDLE_DIR = APP_DIR

ICON_FILE = _BUNDLE_DIR / "movie_icon.ico"
WATCHLIST_FILE = APP_DIR / "watchlist.json"
DIR_WL_FILE = APP_DIR / "director_watchlist.json"
SETTINGS_FILE = APP_DIR / "settings.json"
CACHE_FILE = APP_DIR / "movie_cache.json"

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
C = {
    "bg": "#f5f5f7", "bg2": "#ffffff", "bg3": "#e8e8ed",
    "surface": "#ffffff", "overlay": "#d1d1d6",
    "text": "#1d1d1f", "text2": "#6e6e73", "text3": "#aeaeb2",
    "red": "#ff3b30", "green": "#34c759", "yellow": "#ffcc00",
    "blue": "#007aff", "mauve": "#af52de", "pink": "#ff2d55",
    "teal": "#5ac8fa", "peach": "#ff9500",
    "accent": "#007aff",
    "accent_hover": "#0051a8", "input_bg": "#ffffff", "border": "#d1d1d6",
    "card": "#ffffff", "card_alt": "#f9f9fb", "separator": "#c6c6c8",
}


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
                font=("Segoe UI", 11), relief="flat", borderwidth=1,
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

    def __init__(self, master, current_codes: list[str], on_save):
        super().__init__(master)
        self.title("地域選択")
        self.geometry("520x520")
        self.configure(bg=C["bg"])
        self.transient(master)
        self.grab_set()
        self._on_save = on_save
        self._vars = {}

        hdr = tk.Frame(self, bg=C["bg"], pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="劇場検索する地域を選択", font=("Segoe UI", 14, "bold"),
                 fg=C["text"], bg=C["bg"]).pack(padx=20, anchor="w")
        tk.Label(hdr, text="複数選択可能。劇場ポップアップで表示される地域です。",
                 font=("Segoe UI", 10), fg=C["text2"], bg=C["bg"]).pack(padx=20, anchor="w")

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
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        for region_name, codes in REGION_GROUPS:
            tk.Label(sf, text=region_name, font=("Segoe UI", 11, "bold"),
                     fg=C["text"], bg=C["bg"]).pack(anchor="w", padx=8, pady=(14, 4))
            row = tk.Frame(sf, bg=C["bg"])
            row.pack(fill="x", padx=12)
            for i, code in enumerate(codes):
                var = tk.BooleanVar(value=code in current_codes)
                self._vars[code] = var
                cb = tk.Checkbutton(
                    row, text=PREFECTURES[code], variable=var,
                    bg=C["bg"], fg=C["text"], selectcolor=C["bg2"],
                    activebackground=C["bg"], activeforeground=C["accent"],
                    font=("Segoe UI", 10),
                )
                cb.grid(row=i // 4, column=i % 4, sticky="w", padx=4, pady=2)

        bf = tk.Frame(self, bg=C["bg"])
        bf.pack(fill="x", padx=20, pady=12)
        tk.Button(bf, text="保存", font=("Segoe UI", 11, "bold"), bg=C["accent"], fg="white",
                  relief="flat", cursor="hand2", bd=0, padx=20, pady=6,
                  command=self._save).pack(side="right", padx=(8, 0))
        tk.Button(bf, text="キャンセル", font=("Segoe UI", 10), bg=C["bg3"], fg=C["text2"],
                  relief="flat", cursor="hand2", bd=0, padx=16, pady=6,
                  command=self.destroy).pack(side="right")
        tk.Button(bf, text="全解除", font=("Segoe UI", 9), bg=C["bg3"], fg=C["text3"],
                  relief="flat", cursor="hand2", bd=0, padx=10, pady=4,
                  command=self._clear_all).pack(side="left")
        tk.Button(bf, text="関東選択", font=("Segoe UI", 9), bg=C["bg3"], fg=C["text3"],
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
        self.root.title("映画上映チェッカー")
        self.root.geometry("1160x750")
        self.root.minsize(1000, 600)
        self.root.configure(bg=C["bg"])
        if ICON_FILE.exists():
            try:
                self.root.iconbitmap(str(ICON_FILE))
            except tk.TclError:
                pass
        self.watchlist = load_json(WATCHLIST_FILE)
        self.dir_watchlist = load_json(DIR_WL_FILE)
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
        self.F = {
            "title": tkfont.Font(family="Segoe UI", size=16, weight="bold"),
            "h2": tkfont.Font(family="Segoe UI", size=12, weight="bold"),
            "normal": tkfont.Font(family="Segoe UI", size=11),
            "small": tkfont.Font(family="Segoe UI", size=10),
            "btn": tkfont.Font(family="Segoe UI", size=10, weight="bold"),
            "caption": tkfont.Font(family="Segoe UI", size=9),
        }
        self._setup_styles()
        self._build_ui()
        self._refresh_wl()
        self._show_store_notes()
        self.root.after(300, self._startup_fetch)

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        for name in ("WL.Treeview", "NS.Treeview"):
            rh = 40 if name.startswith("WL") else 34
            s.configure(name, background=C["card"], foreground=C["text"], fieldbackground=C["card"],
                        font=("Segoe UI", 11), rowheight=rh, borderwidth=0)
            s.configure(f"{name}.Heading", background=C["bg"], foreground=C["text2"],
                        font=("Segoe UI", 10, "bold"), borderwidth=0, relief="flat", padding=(8, 6))
            s.map(name, background=[("selected", "#e3f0ff")], foreground=[("selected", C["accent"])])
        s.configure("Prog.Horizontal.TProgressbar", troughcolor=C["bg3"], background=C["accent"],
                    borderwidth=0, thickness=4)
        s.configure("TCombobox", fieldbackground=C["input_bg"], foreground=C["text"], padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", C["input_bg"])], foreground=[("readonly", C["text"])])

    def _build_ui(self):
        hdr = tk.Frame(self.root, bg=C["card"], pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="映画上映チェッカー", font=self.F["title"], fg=C["text"], bg=C["card"]).pack(side="left", padx=20)

        pf = tk.Frame(hdr, bg=C["card"])
        pf.pack(side="right", padx=20)
        tk.Label(pf, text="地域:", font=self.F["small"], fg=C["text2"], bg=C["card"]).pack(side="left", padx=(0, 4))
        self.pref_label = tk.Label(pf, text="", font=self.F["small"], fg=C["text"], bg=C["card"])
        self.pref_label.pack(side="left", padx=(0, 8))
        tk.Button(pf, text="変更", font=self.F["small"], bg=C["bg3"], fg=C["text2"], relief="flat", cursor="hand2",
                  padx=10, pady=3, bd=0, command=self._open_region_selector).pack(side="left")
        tk.Button(pf, text="✉ メール", font=self.F["small"], bg=C["bg3"], fg=C["text2"], relief="flat", cursor="hand2",
                  padx=10, pady=3, bd=0, command=self._open_mail_settings).pack(side="left", padx=(8, 0))
        self._update_pref_label()

        self.status_lbl = tk.Label(hdr, text="", font=self.F["caption"], fg=C["text3"], bg=C["card"])
        self.status_lbl.pack(side="right", padx=20)

        tk.Frame(self.root, bg=C["separator"], height=1).pack(fill="x")

        self.progress = ttk.Progressbar(self.root, style="Prog.Horizontal.TProgressbar", mode="determinate", maximum=11)

        main = tk.PanedWindow(self.root, orient="horizontal", bg=C["bg"], sashwidth=1, sashrelief="flat")
        main.pack(fill="both", expand=True, padx=0, pady=0)
        self._main_pw = main

        left = tk.Frame(main, bg=C["bg"])
        main.add(left, width=540, minsize=400)
        self._build_left(left)

        right = tk.Frame(main, bg=C["bg"])
        main.add(right, minsize=400)
        self._build_right(right)

    def _build_left(self, p):
        self._left_panel = p
        self._left_mode = "movie"

        tab_bar = tk.Frame(p, bg=C["bg"])
        tab_bar.pack(fill="x", padx=10, pady=(10, 0))
        self._ltab_btn_movie = tk.Button(
            tab_bar, text="📋 映画WL", font=self.F["btn"], bg=C["accent"], fg="white", relief="flat", cursor="hand2",
            padx=14, pady=3, command=lambda: self._switch_left_tab("movie"))
        self._ltab_btn_movie.pack(side="left")
        self._ltab_btn_dir = tk.Button(
            tab_bar, text="🎬 監督WL", font=self.F["btn"], bg=C["bg3"], fg=C["text2"], relief="flat", cursor="hand2",
            padx=14, pady=3, command=lambda: self._switch_left_tab("director"))
        self._ltab_btn_dir.pack(side="left", padx=(2, 0))
        self.wl_cnt = tk.Label(tab_bar, text="0件", font=self.F["small"], fg=C["text3"], bg=C["bg"])
        self.wl_cnt.pack(side="right")
        self.wl_hint = tk.Label(tab_bar, text="ダブルクリックで劇場検索", font=self.F["small"], fg=C["text3"], bg=C["bg"])
        self.wl_hint.pack(side="right", padx=(0, 8))

        self._movie_wl_frame = tk.Frame(p, bg=C["bg"])
        self._build_movie_wl_view(self._movie_wl_frame)
        self._dir_wl_frame = tk.Frame(p, bg=C["bg"])
        self._build_dir_wl_view(self._dir_wl_frame)
        self._movie_wl_frame.pack(fill="both", expand=True)

    def _switch_left_tab(self, mode: str):
        if mode == self._left_mode:
            return
        self._left_mode = mode
        if mode == "movie":
            self._dir_wl_frame.pack_forget()
            self._movie_wl_frame.pack(fill="both", expand=True)
            self._ltab_btn_movie.config(bg=C["accent"], fg="white")
            self._ltab_btn_dir.config(bg=C["bg3"], fg=C["text2"])
            self.wl_cnt.config(text=f"{len(self.watchlist)}件")
            self.wl_hint.config(text="ダブルクリックで劇場検索")
        else:
            self._movie_wl_frame.pack_forget()
            self._dir_wl_frame.pack(fill="both", expand=True)
            self._ltab_btn_dir.config(bg=C["accent"], fg="white")
            self._ltab_btn_movie.config(bg=C["bg3"], fg=C["text2"])
            self.wl_cnt.config(text=f"{len(self.dir_watchlist)}人")
            self.wl_hint.config(text="ダブルクリックで劇場/作品ページ")
            self._refresh_dir_wl()

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
        self.wl_tree.column("status", width=80, minwidth=60, anchor="center")
        self.wl_tree.column("title", width=220, minwidth=120)
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
        self._btn(bb, "上映チェック", self._check_status, C["green"]).pack(side="left")
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
                                         font=("Segoe UI", 10), width=20)
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
        self._btn(bb, "上映チェック", self._check_dir_wl_status, C["green"]).pack(side="left")
        self._btn(bb, "劇場を探す", self._theater_for_dir_wl, C["blue"]).pack(side="left", padx=(8, 0))
        self._btn(bb, "作品再取得", self._refetch_dir_filmography, C["bg3"]).pack(side="right")

    def _build_right(self, p):
        hdr = tk.Frame(p, bg=C["bg"])
        hdr.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(hdr, text="上映中 / 監督検索", font=self.F["h2"], fg=C["text"], bg=C["bg"]).pack(side="left")
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
        self._btn(sf, "手動取得", self._fetch_movies, C["bg3"]).pack(side="right")

        tf = tk.Frame(p, bg=C["bg"])
        tf.pack(fill="both", expand=True, padx=16, pady=(8, 4))
        self.ns_tree = ttk.Treeview(tf, style="NS.Treeview", columns=("no", "title", "director"),
                                    show="headings", selectmode="browse")
        self.ns_tree.heading("no", text="#")
        self.ns_tree.heading("title", text="タイトル")
        self.ns_tree.heading("director", text="監督")
        self.ns_tree.column("no", width=36, minwidth=30, anchor="center")
        self.ns_tree.column("title", width=290, minwidth=160)
        self.ns_tree.column("director", width=130, minwidth=80)
        sc = ttk.Scrollbar(tf, orient="vertical", command=self.ns_tree.yview)
        self.ns_tree.configure(yscrollcommand=sc.set)
        self.ns_tree.pack(side="left", fill="both", expand=True)
        sc.pack(side="right", fill="y")
        self.ns_tree.bind("<Double-1>", self._on_ns_dblclick)
        self.search_var.trace_add("write", lambda *a: self._filter_ns())

        bb = tk.Frame(p, bg=C["bg"])
        bb.pack(fill="x", padx=16, pady=(4, 14))
        self._btn(bb, "ウォッチリストに追加", self._add_from_ns, C["accent"]).pack(side="left")
        self._btn(bb, "劇場を探す", self._theater_for_ns, C["blue"]).pack(side="left", padx=(8, 0))

    def _btn(self, parent, text, cmd, bg):
        fg = "white" if bg not in (C["bg3"],) else C["text2"]
        return tk.Button(parent, text=text, font=self.F["btn"], bg=bg, fg=fg, activebackground=C["accent_hover"],
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
        self.status_lbl.config(text=msg)
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

    def _open_mail_settings(self):
        MailSettingsDialog(self)

    def run_notify(self, kind: str = "auto") -> dict:
        """別スレッドから呼ぶ。kind = auto | digest | test。戻りは notify.run の物。"""
        return notify.run(self.settings, APP_DIR, list(self.watchlist), list(self.dir_watchlist), dict(self.now_showing),
                          kind=kind, theaters_fn=fetch_theaters_multi)

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

        threading.Thread(target=work, daemon=True).start()

    def _open_region_selector(self):
        RegionSelector(self.root, self.pref_codes, self._on_regions_saved)

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
                if datetime.now() - last_dt < timedelta(hours=6):
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

    def _auto_update(self):
        if self.is_fetching:
            self._schedule_update()
            return
        self._status("📡 自動更新中...")
        self.is_fetching = True

        def work():
            ms = fetch_now_showing()
            rk = fetch_ranking()
            d = {m["title"]: m for m in ms + rk}
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
        self.is_fetching = True
        self._status("📡 eiga.com から映画情報を取得中...")
        self._show_progress(True)
        self.progress["value"] = 0

        def work():
            def prog(p, t):
                self.root.after(0, lambda: self._on_prog(p, t))

            ms = fetch_now_showing(progress_cb=prog)
            rk = fetch_ranking()
            d = {m["title"]: m for m in ms + rk}
            self.root.after(0, lambda: self._on_fetch_done(d))

        threading.Thread(target=work, daemon=True).start()

    def _on_prog(self, page, total):
        self.progress["value"] = page
        self._status(f"📡 取得中... ページ {page} ({total}件)")

    def _fetch_got_nothing(self, movies) -> bool:
        """取得が空で訳がある時: 前の一覧とキャッシュは守り、訳だけ状態欄に出す (v3.0 は空で上書きしていた)。"""
        if movies or not self._fetch_failed_text():
            return False
        self.is_fetching = False
        self._show_progress(False)
        self._status(f"⚠ 0件: {self._fetch_failed_text()}")
        self._schedule_update()
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
        self._maybe_notify()
        self._schedule_update()

    def _on_data_ready(self, cache=False, ts=""):
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

    def _save_cache(self, ts):
        save_json(CACHE_FILE, {"updated": ts, "movies": list(self.now_showing.values())})

    # ---- 右: 上映中の一覧 (検索で絞る。監督名なら監督 WL / 映画 WL の未上映も並べる) ----
    def _filter_ns(self):
        self.ns_tree.delete(*self.ns_tree.get_children())
        self._ns_extra = {}
        raw = self.search_var.get()
        kw = ""
        if raw and raw != "🔍 タイトルまたは監督名で検索...":
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
        for title, info in self.now_showing.items():
            director = info.get("director", "")
            if kw:
                if kw not in normalize(title) and kw not in normalize(director):
                    continue
            n += 1
            marker = "🟢" if (is_dir and kw) else str(n)
            self.ns_tree.insert("", "end", iid=title, values=(marker, title, director))
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
                    self.ns_tree.insert("", "end", iid=iid, values=("⬜", t, item["name"]))
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
                self.ns_tree.insert("", "end", iid=iid, values=("⬜", t, d))
                self._ns_extra[iid] = {"title": t, "movie_id": w.get("movie_id", ""), "url": w.get("url", ""), "director": d}
                extra += 1
        if kw:
            if is_dir:
                self.ns_cnt.config(text=f"🟢{n} ⬜{extra} / {len(self.now_showing)}件")
            else:
                self.ns_cnt.config(text=f"{n}/{len(self.now_showing)}件")

    # ---- 左: 映画のウォッチリスト ----
    def _refresh_wl(self):
        self.wl_tree.delete(*self.wl_tree.get_children())
        for item in self.watchlist:
            st = item.get("status", "⏳ 未チェック")
            director = item.get("director", "")
            info = "🎬 劇場を検索 →" if "上映中" in st else item.get("added_date", "")
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
                rk = fetch_ranking()
                d = {m["title"]: m for m in ms + rk}
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
        self._status(f"✅「{title}」をウォッチリストに追加")

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
                rk = fetch_ranking()
                d = {m["title"]: m for m in ms + rk}
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
        popup.title(f"{title} - 劇場検索")
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
                result = fetch_theaters_multi(movie_id, pref_codes)
                popup.after(0, lambda: show(result))

            threading.Thread(target=work, daemon=True).start()

        def show(result: dict[str, list[dict]]):
            loading.destroy()
            container.pack(fill="both", expand=True, padx=20, pady=8)
            total = sum(len(v) for v in result.values())
            if not total:
                tk.Label(container, text="選択地域では上映館が見つかりませんでした。\n\n「変更」で別の地域を追加するか、eiga.comで確認してください。",
                         font=self.F["normal"], fg=C["text2"], bg=C["bg"], wraplength=520, justify="left").pack(pady=24)
                tk.Button(container, text="eiga.com で劇場を探す", font=self.F["btn"], bg=C["accent"], fg="white", relief="flat",
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
                canvas.yview_scroll(-1 * (e.delta // 120), "units")

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
        tk.Button(bf, text="eiga.com で全地域を見る", font=self.F["btn"], bg=C["bg3"], fg=C["text2"], relief="flat", cursor="hand2",
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
    rk = fetch_ranking()
    now_showing = {m["title"]: m for m in ms + rk}
    if not now_showing:
        _say("取得できませんでした: " + (fetch.error_text(200) or "0 件"))
        return 2
    save_json(CACHE_FILE, {"updated": datetime.now().strftime("%Y-%m-%d %H:%M"), "movies": list(now_showing.values())})
    r = notify.run(settings, APP_DIR, wl, dwl, now_showing, kind="auto", theaters_fn=fetch_theaters_multi)
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
