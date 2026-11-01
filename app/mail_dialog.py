"""設定の窓『メール通知』(MV-2 / MV-6)。3 つのタブ: 送り元 (雛形と手順) / 送る条件 / 記録。
『保存』『試しに送る』『今すぐまとめを送る』はタブの外にあり、3 タブの値をまとめて保存する。"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from macui import Button  # Mac は色が効く平らなボタン、Windows は tk.Button

from moviecheck import notify

FIELD_HELP = {
    "to": "受け取るアドレス (自分のアドレスでよい)",
    "host": "送信サーバー。雛形のボタンで入る",
    "user": "送り元のメールアドレス全体 (例の形: name@gmail.com)",
    "password": "アプリ パスワード。Windows の資格情報に置き、settings.json には書かない",
}


class MailSettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        C = app.C
        self.C = C
        self.title("メール通知の設定")
        self.configure(bg=C["bg"])
        self.transient(app.root)
        self.grab_set()
        self.resizable(False, False)
        cfg = notify.cfg_of(app.settings)
        self.cfg0 = cfg
        self.v = {
            "to": tk.StringVar(value=cfg["to"]), "host": tk.StringVar(value=cfg["host"]),
            "port": tk.StringVar(value=str(cfg["port"])), "user": tk.StringVar(value=cfg["user"]),
            "mode": tk.StringVar(value=cfg["mode"]), "password": tk.StringVar(value=""),
            "on_change": tk.BooleanVar(value=bool(cfg["on_change"])),
            "weekly": tk.BooleanVar(value=bool(cfg["weekly"]["enabled"])),
            "weekday": tk.StringVar(value=notify.WEEKDAYS[int(cfg["weekly"]["weekday"])]),
            "hour": tk.StringVar(value=str(cfg["weekly"]["hour"])),
        }
        self.font = app.F["normal"]
        self.small = app.F["caption"]
        outer = tk.Frame(self, bg=C["bg"], padx=16, pady=12)
        outer.pack(fill="both")
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="both")
        self.tab_src = tk.Frame(self.nb, bg=C["bg"], padx=14, pady=10)
        self.tab_when = tk.Frame(self.nb, bg=C["bg"], padx=14, pady=10)
        self.tab_log = tk.Frame(self.nb, bg=C["bg"], padx=14, pady=10)
        self.nb.add(self.tab_src, text="  1. 送り元  ")
        self.nb.add(self.tab_when, text="  2. 送る条件  ")
        self.nb.add(self.tab_log, text="  3. 記録  ")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._on_tab())
        self._build_src()
        self._build_when()
        self._build_log()

        self.msg = tk.Label(outer, text="", font=self.small, fg=C["text2"], bg=C["bg"], wraplength=640, justify="left")
        self.msg.pack(fill="x", pady=(8, 0), anchor="w")
        bf = tk.Frame(outer, bg=C["bg"])
        bf.pack(fill="x", pady=(8, 0))
        tk.Label(bf, text="保存は 3 つのタブの値をまとめて書きます", font=self.small, fg=C["text3"], bg=C["bg"]).pack(side="left")

        def btn(text, cmd, primary=False):
            return Button(bf, text=text, font=app.F["btn"], bg=C["accent"] if primary else C["bg3"], fg="white" if primary else C["text2"],
                             relief="flat", cursor="hand2", bd=0, padx=12, pady=5, command=cmd)

        btn("閉じる", self.destroy).pack(side="right", padx=4)
        btn("保存", self._save, primary=True).pack(side="right", padx=4)
        btn("今すぐまとめを送る", lambda: self._send("digest")).pack(side="right", padx=4)
        btn("試しに送る", lambda: self._send("test")).pack(side="right", padx=4)

    # ---- タブ 1: 送り元 ----
    def _build_src(self):
        C, f = self.C, self.tab_src
        tk.Label(f, text="どこから送るか (雛形を押すと host と port が入り、右に手順が出ます)", font=self.font, fg=C["text"], bg=C["bg"]).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        pf = tk.Frame(f, bg=C["bg"])
        pf.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
        for name in notify.PRESETS:
            Button(pf, text=name, font=self.small, bg=C["bg3"], fg=C["text2"], relief="flat", cursor="hand2", bd=0, padx=10, pady=3,
                      command=lambda n=name: self._apply_preset(n)).pack(side="left", padx=(0, 6))

        left = tk.Frame(f, bg=C["bg"])
        left.grid(row=2, column=0, sticky="nw")
        r = 0

        def row(label, widget, help_key=None):
            nonlocal r
            tk.Label(left, text=label, font=self.font, fg=C["text2"], bg=C["bg"]).grid(row=r, column=0, sticky="ne", padx=(0, 8), pady=(4, 0))
            widget.grid(row=r, column=1, sticky="w", pady=(4, 0))
            if help_key:
                tk.Label(left, text=FIELD_HELP[help_key], font=self.small, fg=C["text3"], bg=C["bg"], wraplength=300, justify="left").grid(row=r + 1, column=1, sticky="w")
            r += 2

        row("宛先", tk.Entry(left, textvariable=self.v["to"], width=34, font=self.font, bg=C["input_bg"], fg=C["text"], insertbackground=C["text"], relief="flat", highlightthickness=1, highlightbackground=C["border"], highlightcolor=C["accent"]), "to")
        row("SMTP host", tk.Entry(left, textvariable=self.v["host"], width=34, font=self.font, bg=C["input_bg"], fg=C["text"], insertbackground=C["text"], relief="flat", highlightthickness=1, highlightbackground=C["border"], highlightcolor=C["accent"]), "host")
        pm = tk.Frame(left, bg=C["bg"])
        tk.Entry(pm, textvariable=self.v["port"], width=6, font=self.font, bg=C["input_bg"], fg=C["text"], insertbackground=C["text"], relief="flat", highlightthickness=1, highlightbackground=C["border"], highlightcolor=C["accent"]).pack(side="left")
        ttk.Radiobutton(pm, text="SSL (465)", variable=self.v["mode"], value="ssl").pack(side="left", padx=(10, 4))
        ttk.Radiobutton(pm, text="STARTTLS (587)", variable=self.v["mode"], value="starttls").pack(side="left")
        row("port / 方式", pm)
        row("SMTP user", tk.Entry(left, textvariable=self.v["user"], width=34, font=self.font, bg=C["input_bg"], fg=C["text"], insertbackground=C["text"], relief="flat", highlightthickness=1, highlightbackground=C["border"], highlightcolor=C["accent"]), "user")
        pw = tk.Frame(left, bg=C["bg"])
        tk.Entry(pw, textvariable=self.v["password"], width=22, font=self.font, show="*", bg=C["input_bg"], fg=C["text"], insertbackground=C["text"], relief="flat", highlightthickness=1, highlightbackground=C["border"], highlightcolor=C["accent"]).pack(side="left")
        saved = notify.has_password(self.cfg0["user"])
        self.pw_note = tk.Label(pw, text="(保存済み。変える時だけ)" if saved else "(未保存)", font=self.small, fg=C["text3"], bg=C["bg"])
        self.pw_note.pack(side="left", padx=6)
        row("パスワード", pw, "password")

        right = tk.Frame(f, bg=C["card"], padx=10, pady=8, highlightthickness=1, highlightbackground=C["border"])
        right.grid(row=2, column=1, sticky="nsew", padx=(16, 0))
        self.guide_title = tk.Label(right, text="手順", font=self.app.F["btn"], fg=C["text"], bg=C["card"], anchor="w")
        self.guide_title.pack(fill="x")
        self.guide = tk.Label(right, text="上の雛形を押すと、ここに『どこで何をするか』が出ます。\n\n雛形は " + notify.PRESETS_CHECKED + " に確かめた物です。提供元が方式を変える事があります。",
                              font=self.small, fg=C["text2"], bg=C["card"], justify="left", anchor="nw", wraplength=300)
        self.guide.pack(fill="both", expand=True)
        self._current_preset = None

    def _apply_preset(self, name: str):
        p = notify.PRESETS[name]
        self._current_preset = name
        self.guide_title.config(text=f"{name} の手順")
        if not p["usable"]:
            self.guide.config(text=p["note"], fg=self.C["red"])
            return
        self.v["host"].set(p["host"])
        self.v["port"].set(str(p["port"]))
        self.v["mode"].set(p["mode"])
        lines = [f"{i}. {s}" for i, s in enumerate(p["steps"], 1)]
        if p["note"]:
            lines.append("")
            lines.append("※ " + p["note"])
        self.guide.config(text="\n".join(lines) if lines else "", fg=self.C["text2"])
        self.msg.config(text=f"✅ {name} の host / port / 方式を入れました。user とパスワードを入れて『試しに送る』", fg=self.C["green"])

    # ---- タブ 2: 送る条件 ----
    def _build_when(self):
        C, f = self.C, self.tab_when
        tk.Label(f, text="いつ送るか", font=self.font, fg=C["text"], bg=C["bg"]).pack(anchor="w", pady=(0, 6))
        ttk.Checkbutton(f, text="新しく上映になった物があった時に送る (取得のたび。観たい映画・監督の作品)", variable=self.v["on_change"]).pack(anchor="w")
        wk = tk.Frame(f, bg=C["bg"])
        ttk.Checkbutton(wk, text="毎週のまとめを送る:", variable=self.v["weekly"]).pack(side="left")
        ttk.Combobox(wk, textvariable=self.v["weekday"], values=notify.WEEKDAYS, width=3, state="readonly").pack(side="left", padx=(6, 2))
        tk.Label(wk, text="曜日", font=self.font, fg=C["text2"], bg=C["bg"]).pack(side="left")
        ttk.Combobox(wk, textvariable=self.v["hour"], values=[str(h) for h in range(24)], width=3, state="readonly").pack(side="left", padx=(8, 2))
        tk.Label(wk, text="時以降 (その日 1 回)", font=self.font, fg=C["text2"], bg=C["bg"]).pack(side="left")
        wk.pack(anchor="w", pady=(4, 10))
        _mac = sys.platform == "darwin"
        tk.Label(f, text="送るのはアプリが動いている時です。閉じていても送るには、" + ("Mac の crontab に次を登録します:" if _mac else "Windows のタスク スケジューラに次を登録します:"),
                 font=self.small, fg=C["text2"], bg=C["bg"], wraplength=620, justify="left").pack(anchor="w")
        cmd = self.app.send_mail_command()
        e = tk.Entry(f, font=self.small, width=90, bg=C["input_bg"], fg=C["text"], insertbackground=C["text"], relief="flat", highlightthickness=1, highlightbackground=C["border"], highlightcolor=C["accent"])
        e.insert(0, cmd)
        e.config(state="readonly")
        e.pack(anchor="w", pady=(4, 2))
        tk.Label(f, text=("ターミナルで crontab -e を開き『0 8 * * * 上の行』を 1 行足します (毎日 8:00)。" if _mac else
                          "タスク スケジューラ → 基本タスクの作成 → トリガー『毎日』(例: 8:00) → 操作『プログラムの開始』に上の行 (プログラム = exe、引数 = --send-mail)。")
                         + "取得 → 判定 → 送信 → 終了し、結果は『3. 記録』に残ります。",
                 font=self.small, fg=C["text3"], bg=C["bg"], wraplength=620, justify="left").pack(anchor="w")

    # ---- タブ 3: 記録 ----
    def _build_log(self):
        C, f = self.C, self.tab_log
        self.log_head = tk.Label(f, text="", font=self.font, fg=C["text"], bg=C["bg"], justify="left", anchor="w")
        self.log_head.pack(fill="x", pady=(0, 6))
        self.log_box = tk.Listbox(f, font=self.small, width=90, height=14, bg=C["card"], fg=C["text2"], relief="flat", highlightthickness=1, highlightbackground=C["border"])
        self.log_box.pack(fill="both")
        tk.Label(f, text="いつ・種類・件数・結果だけ (本文や宛先は残しません)", font=self.small, fg=C["text3"], bg=C["bg"]).pack(anchor="w", pady=(4, 0))
        self._fill_log()

    def _fill_log(self):
        from moviecheck.store import load_json
        app = self.app
        cfg = self._collect()
        state = load_json(app.data_dir() / "notify_state.json") or {}
        nd = notify.next_digest(cfg, state if isinstance(state, dict) else {}, datetime.now())
        entries = notify.recent_log(app.data_dir(), 20)
        last = next((e for e in entries if e.get("kind") != "skip"), None)
        head = [f"次のまとめ: {nd.strftime('%m/%d')} ({notify.WEEKDAYS[nd.weekday()]}) {nd.hour} 時以降" if nd else "次のまとめ: 無し (条件のタブで有効に)"]
        head.append("最後に送った: " + (notify.log_line(last) if last else "まだ送っていません"))
        self.log_head.config(text="\n".join(head))
        self.log_box.delete(0, "end")
        for e in entries:
            self.log_box.insert("end", notify.log_line(e))
        if not entries:
            self.log_box.insert("end", "(記録なし)")

    def _on_tab(self):
        if self.nb.index(self.nb.select()) == 2:
            self._fill_log()

    # ---- 保存と送信 (今までと同じ流れ) ----
    def _collect(self) -> dict:
        v = self.v
        try:
            port = int(v["port"].get().strip() or 0)
        except ValueError:
            port = 0
        return {"to": v["to"].get().strip(), "host": v["host"].get().strip(), "port": port, "user": v["user"].get().strip(),
                "mode": v["mode"].get(), "on_change": bool(v["on_change"].get()),
                "weekly": {"enabled": bool(v["weekly"].get()), "weekday": notify.WEEKDAYS.index(v["weekday"].get()),
                           "hour": int(v["hour"].get() or 8)}}

    def _save(self, quiet=False) -> bool:
        cfg = self._collect()
        prob = notify.cfg_problem(cfg)
        if prob:
            self.nb.select(0)
            self.msg.config(text=f"⚠ {prob} → 『1. 送り元』で入れる", fg=self.C["red"])
            return False
        pw = self.v["password"].get()
        if pw:
            err = notify.set_password(cfg["user"], pw)
            if err:
                self.msg.config(text=f"⚠ {err}", fg=self.C["red"])
                return False
            self.v["password"].set("")
            self.pw_note.config(text="(保存済み。変える時だけ)")
        self.app.settings["mail"] = cfg
        self.app.save_settings()
        if not quiet:
            note = "パスワードは資格情報に" if pw else ("パスワードは未保存 → 『パスワード』に入れて保存" if not notify.has_password(cfg["user"]) else "")
            self.msg.config(text="✅ 保存しました" + (f" ({note})" if note else ""), fg=self.C["green"])
        self.app.refresh_bar()
        return True

    def _send(self, kind: str):
        if not self._save(quiet=True):
            return
        self.msg.config(text="✉ 送っています…", fg=self.C["text2"])
        app = self.app

        def work():
            r = app.run_notify(kind)
            self.after(0, lambda: done(r))

        def done(r):
            if r.get("error"):
                self.msg.config(text=f"⚠ {r['error']}\n→ {notify.hint(r['error'])}", fg=self.C["red"])
            elif r.get("sent"):
                self.msg.config(text=f"✅ 送りました ({r['count']} 件)。届いていなければ迷惑メールも見る", fg=self.C["green"])
            else:
                self.msg.config(text=f"送っていません ({r.get('why', '')})", fg=self.C["text2"])
            self._fill_log()
            app.refresh_bar()

        threading.Thread(target=work, daemon=True).start()


def show_error_once(parent, title, text):
    messagebox.showwarning(title, text, parent=parent)
