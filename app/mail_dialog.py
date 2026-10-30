"""設定の窓『メール通知』(MV-2)。宛先・SMTP・user・パスワード (資格情報へ)・タイミング、試しに送る、今すぐまとめを送る。"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from moviecheck import notify


class MailSettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        C = app.C
        self.title("メール通知の設定")
        self.configure(bg=C["bg"])
        self.transient(app.root)
        self.grab_set()
        self.resizable(False, False)
        cfg = notify.cfg_of(app.settings)
        self.v = {
            "to": tk.StringVar(value=cfg["to"]), "host": tk.StringVar(value=cfg["host"]),
            "port": tk.StringVar(value=str(cfg["port"])), "user": tk.StringVar(value=cfg["user"]),
            "mode": tk.StringVar(value=cfg["mode"]), "password": tk.StringVar(value=""),
            "on_change": tk.BooleanVar(value=bool(cfg["on_change"])),
            "weekly": tk.BooleanVar(value=bool(cfg["weekly"]["enabled"])),
            "weekday": tk.StringVar(value=notify.WEEKDAYS[int(cfg["weekly"]["weekday"])]),
            "hour": tk.StringVar(value=str(cfg["weekly"]["hour"])),
        }
        f = tk.Frame(self, bg=C["bg"], padx=20, pady=14)
        f.pack(fill="both")
        font = app.F["normal"]

        def row(r, label, widget):
            tk.Label(f, text=label, font=font, fg=C["text2"], bg=C["bg"]).grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
            widget.grid(row=r, column=1, sticky="w", pady=3)

        row(0, "宛先", tk.Entry(f, textvariable=self.v["to"], width=36, font=font))
        row(1, "SMTP host", tk.Entry(f, textvariable=self.v["host"], width=36, font=font))
        pm = tk.Frame(f, bg=C["bg"])
        tk.Entry(pm, textvariable=self.v["port"], width=6, font=font).pack(side="left")
        ttk.Radiobutton(pm, text="SSL (465)", variable=self.v["mode"], value="ssl").pack(side="left", padx=(10, 4))
        ttk.Radiobutton(pm, text="STARTTLS (587)", variable=self.v["mode"], value="starttls").pack(side="left")
        row(2, "port", pm)
        row(3, "SMTP user", tk.Entry(f, textvariable=self.v["user"], width=36, font=font))
        pw = tk.Frame(f, bg=C["bg"])
        tk.Entry(pw, textvariable=self.v["password"], width=24, font=font, show="*").pack(side="left")
        saved = notify.has_password(cfg["user"])
        self.pw_note = tk.Label(pw, text="(資格情報に保存済み。変える時だけ入力)" if saved else "(未保存)", font=app.F["caption"], fg=C["text3"], bg=C["bg"])
        self.pw_note.pack(side="left", padx=6)
        row(4, "パスワード", pw)
        tk.Label(f, text="パスワードは Windows の資格情報マネージャーに置き、settings.json には書きません。Gmail は『アプリ パスワード』。",
                 font=app.F["caption"], fg=C["text3"], bg=C["bg"], wraplength=420, justify="left").grid(row=5, column=1, sticky="w", pady=(0, 8))
        ttk.Checkbutton(f, text="新しく上映になった物があった時に送る (取得のたび)", variable=self.v["on_change"]).grid(row=6, column=1, sticky="w")
        wk = tk.Frame(f, bg=C["bg"])
        ttk.Checkbutton(wk, text="毎週のまとめを送る:", variable=self.v["weekly"]).pack(side="left")
        ttk.Combobox(wk, textvariable=self.v["weekday"], values=notify.WEEKDAYS, width=3, state="readonly").pack(side="left", padx=(6, 2))
        tk.Label(wk, text="曜日", font=font, fg=C["text2"], bg=C["bg"]).pack(side="left")
        ttk.Combobox(wk, textvariable=self.v["hour"], values=[str(h) for h in range(24)], width=3, state="readonly").pack(side="left", padx=(8, 2))
        tk.Label(wk, text="時以降 (アプリが動いている時、または --send-mail)", font=font, fg=C["text2"], bg=C["bg"]).pack(side="left")
        wk.grid(row=7, column=1, sticky="w", pady=(2, 8))

        bf = tk.Frame(f, bg=C["bg"])
        bf.grid(row=8, column=0, columnspan=2, sticky="e", pady=(6, 0))
        self.msg = tk.Label(f, text="", font=app.F["caption"], fg=C["text2"], bg=C["bg"], wraplength=440, justify="left")
        self.msg.grid(row=9, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def btn(text, cmd, primary=False):
            return tk.Button(bf, text=text, font=app.F["btn"], bg=C["accent"] if primary else C["bg3"], fg="white" if primary else C["text2"],
                             relief="flat", cursor="hand2", bd=0, padx=12, pady=5, command=cmd)

        btn("試しに送る", lambda: self._send("test")).pack(side="left", padx=4)
        btn("今すぐまとめを送る", lambda: self._send("digest")).pack(side="left", padx=4)
        btn("保存", self._save, primary=True).pack(side="left", padx=4)
        btn("閉じる", self.destroy).pack(side="left", padx=4)

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
            self.msg.config(text=f"⚠ {prob}", fg=self.app.C["red"])
            return False
        pw = self.v["password"].get()
        if pw:
            err = notify.set_password(cfg["user"], pw)
            if err:
                self.msg.config(text=f"⚠ {err}", fg=self.app.C["red"])
                return False
            self.v["password"].set("")
            self.pw_note.config(text="(資格情報に保存済み。変える時だけ入力)")
        self.app.settings["mail"] = cfg
        self.app.save_settings()
        if not quiet:
            note = "パスワードは資格情報に" if pw else ("パスワードは未保存" if not notify.has_password(cfg["user"]) else "")
            self.msg.config(text="✅ 保存しました" + (f" ({note})" if note else ""), fg=self.app.C["green"])
        return True

    def _send(self, kind: str):
        if not self._save(quiet=True):
            return
        self.msg.config(text="✉ 送っています…", fg=self.app.C["text2"])
        app = self.app

        def work():
            r = app.run_notify(kind)
            self.after(0, lambda: done(r))

        def done(r):
            if r.get("error"):
                self.msg.config(text=f"⚠ {r['error']}", fg=app.C["red"])
            elif r.get("sent"):
                self.msg.config(text=f"✅ 送りました ({r['count']} 件)", fg=app.C["green"])
            else:
                self.msg.config(text=f"送っていません ({r.get('why', '')})", fg=app.C["text2"])

        threading.Thread(target=work, daemon=True).start()


def show_error_once(parent, title, text):
    messagebox.showwarning(title, text, parent=parent)
