"""Mac 用の部品 (MV-mac)。

macOS の Tk (Aqua) は tk.Button の bg を無視して OS 標準のボタンで描くため、色を付けたボタンが白地に白文字になる。
Mac だけ、色がそのまま効く平らなボタン (Label ベース。hover と押下で色が変わり、Return / space でも押せる) に差し替える。
Windows では今までどおり tk.Button (ここは通らない)。使い方: ``from macui import Button`` → tk.Button と同じ引数で呼ぶ。
"""
from __future__ import annotations

import sys
import tkinter as tk

# tk.Button にはあって tk.Label には渡せない (または自前で扱う) 指定
_BUTTON_ONLY = ("command", "activebackground", "activeforeground", "overrelief", "default", "repeatdelay", "repeatinterval")


def _shade(widget: tk.Misc, color: str, amount: float = 0.10) -> str:
    """hover 用の色。明るい地なら少し暗く、暗い地なら少し明るく (Apple の押し物と同じ向き)。"""
    try:
        r, g, b = (v // 257 for v in widget.winfo_rgb(color))
    except tk.TclError:
        return color
    light = (0.299 * r + 0.587 * g + 0.114 * b) > 128
    mix = 0 if light else 255
    r, g, b = (int(c + (mix - c) * amount) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


class FlatButton(tk.Label):
    """tk.Button と同じ引数で作れる平らなボタン。bg / fg / activebackground / activeforeground / command / state が効く。"""

    def __init__(self, master=None, cnf=None, **kw):
        if cnf:
            kw = {**cnf, **kw}
        self._cmd = kw.pop("command", None)
        self._abg = kw.pop("activebackground", None)
        self._afg = kw.pop("activeforeground", None)
        for k in _BUTTON_ONLY:
            kw.pop(k, None)
        kw.setdefault("cursor", "hand2")
        kw.setdefault("takefocus", 1)
        kw.setdefault("padx", 10)
        kw.setdefault("pady", 4)
        super().__init__(master, **kw)
        self._bg = self.cget("bg")
        self._fg = self.cget("fg")
        self._pressed = False
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())
        self.bind("<FocusIn>", lambda e: self._hover(True))
        self.bind("<FocusOut>", lambda e: self._hover(False))

    # ---- tk.Button と同じ口 ----
    def configure(self, cnf=None, **kw):
        if cnf:
            kw = {**cnf, **kw}
        if "command" in kw:
            self._cmd = kw.pop("command")
        if "activebackground" in kw:
            self._abg = kw.pop("activebackground")
        if "activeforeground" in kw:
            self._afg = kw.pop("activeforeground")
        for k in _BUTTON_ONLY:
            kw.pop(k, None)
        result = tk.Label.configure(self, **kw) if kw else None
        if "bg" in kw or "background" in kw:
            self._bg = self.cget("bg")
        if "fg" in kw or "foreground" in kw:
            self._fg = self.cget("fg")
        return result

    config = configure

    def cget(self, key):
        if key == "command":
            return self._cmd
        if key == "activebackground":
            return self._abg
        if key == "activeforeground":
            return self._afg
        return tk.Label.cget(self, key)

    def __setitem__(self, key, value):
        self.configure({key: value})

    def invoke(self):
        if self._disabled():
            return None
        return self._cmd() if self._cmd else None

    def flash(self):
        pass

    # ---- 見た目 ----
    def _disabled(self) -> bool:
        return str(tk.Label.cget(self, "state")) == "disabled"

    def _hover(self, on: bool):
        if self._disabled():
            return
        if on:
            tk.Label.configure(self, bg=self._abg or _shade(self, self._bg), fg=self._afg or self._fg)
        else:
            tk.Label.configure(self, bg=self._bg, fg=self._fg)

    def _on_leave(self, e):
        self._pressed = False
        self._hover(False)

    def _on_press(self, e):
        if self._disabled():
            return
        self._pressed = True
        tk.Label.configure(self, bg=_shade(self, self._abg or self._bg, 0.18), fg=self._afg or self._fg)

    def _on_release(self, e):
        if not self._pressed:
            return
        self._pressed = False
        inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self._hover(inside)
        if inside:
            self.invoke()


Button = FlatButton if sys.platform == "darwin" else tk.Button
