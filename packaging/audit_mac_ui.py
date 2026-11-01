"""Mac の見た目の機械点検 (MV-mac)。画面を組み立て、暗い / 白い外観と各ダイアログで、全部品の文字色と背景色のコントラストが
3:1 未満の物、Aqua が自前で描いて bg が効かない tk の部品 (Button / Checkbutton / Radiobutton / Menubutton / Scale) を列挙し、
Mac 用ボタン (macui.FlatButton) の hover・押下・無効化を確かめる。例外は最後にまとめて出す。
使い方: .venv-build-mac/bin/python packaging/audit_mac_ui.py app   (源から動かすのと同じで app/ に json が出来る。git には入らない)
"""
import os, sys, traceback
APP = sys.argv[1]; os.chdir(APP); sys.path.insert(0, APP)
import tkinter as tk
from tkinter import ttk
errors = []
import movie_app, macui
root = tk.Tk()
root.report_callback_exception = lambda e, v, tb: errors.append(''.join(traceback.format_exception(e, v, tb))[-500:])
app = movie_app.MovieApp(root)
style = ttk.Style(root)

def rgb(w, c):
    r, g, b = w.winfo_rgb(c); return (r / 65535, g / 65535, b / 65535)
def lum(c):
    def ch(v): return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c; return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)
def contrast(w, fg, bg):
    a, b = lum(rgb(w, fg)), lum(rgb(w, bg)); hi, lo = max(a, b), min(a, b); return (hi + 0.05) / (lo + 0.05)
NATIVE = {'Button', 'Checkbutton', 'Radiobutton', 'Menubutton', 'Scale'}   # Aqua が自前で描き、bg が効かない tk の部品

def audit(label):
    low, native, n = [], [], 0
    def walk(w):
        nonlocal n
        for c in w.winfo_children():
            cls = c.winfo_class()
            try:
                if cls.startswith('T') and cls != 'Text' and cls != 'Toplevel':   # ttk: スタイルから
                    st = c.cget('style') or cls
                    fg = style.lookup(st, 'foreground') or style.lookup(cls, 'foreground')
                    bg = style.lookup(st, 'fieldbackground') or style.lookup(st, 'background') or style.lookup(cls, 'background')
                    text = ''
                else:
                    fg = c.cget('fg') if 'fg' in c.keys() else None
                    bg = c.cget('bg') if 'bg' in c.keys() else None
                    text = (c.cget('text') if 'text' in c.keys() else '') or ''
                if cls in NATIVE: native.append((cls, text[:20], fg, bg))
                if fg and bg and str(fg) and str(bg):
                    n += 1; r = contrast(c, fg, bg)
                    if r < 3.0: low.append((round(r, 2), cls, text[:24].replace('\n', ' '), fg, bg))
            except tk.TclError:
                pass
            walk(c)
    walk(root)
    print(f'== {label}: 部品 {n} 件を点検、コントラスト 3:1 未満 {len(low)} 件、Aqua で色が効かない部品 {len(native)} 件')
    for r in sorted(low)[:25]: print('   低', r)
    for r in native[:10]: print('   native', r)
    return low, native

def open_dialogs():
    for name in ('_open_about', '_open_mail_settings', '_open_taste', '_open_region_selector'):
        try: getattr(app, name)(); root.update()
        except Exception as e: errors.append(f'{name}: {e}')
def close_dialogs():
    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel): w.destroy()

def run():
    try:
        print('テーマ:', app.settings.get('theme', 'dark'))
        open_dialogs(); audit('暗い外観 + 各ダイアログ'); close_dialogs()
        app._toggle_theme(); root.update(); print('テーマ:', app.settings.get('theme'))
        open_dialogs(); audit('白い外観 + 各ダイアログ'); close_dialogs()
        app._toggle_theme(); root.update()
        # FlatButton の振る舞い
        found = None
        def find(w):
            nonlocal found
            for c in w.winfo_children():
                if isinstance(c, macui.FlatButton) and str(c.cget('state')) != 'disabled' and not found: found = c
                find(c)
        find(root)
        if found:
            cnt = {'n': 0}; found.configure(command=lambda: cnt.__setitem__('n', cnt['n'] + 1))
            bg0 = found.cget('bg'); found.event_generate('<Enter>'); root.update(); bg1 = tk.Label.cget(found, 'bg')
            found.event_generate('<ButtonPress-1>', x=2, y=2); root.update(); bg2 = tk.Label.cget(found, 'bg')
            found.event_generate('<ButtonRelease-1>', x=2, y=2); root.update()
            found.event_generate('<Leave>'); root.update(); bg3 = tk.Label.cget(found, 'bg')
            print(f'== FlatButton: hover で色が変わる {bg0 != bg1} ({bg0}→{bg1})、押下でさらに変わる {bg2 != bg1}、離して戻る {bg3 == bg0}、command が 1 回 {cnt["n"] == 1}、状態 {found.cget("state")}、文字 "{found.cget("text")}"')
            found.configure(state='disabled'); found.event_generate('<ButtonPress-1>', x=2, y=2); found.event_generate('<ButtonRelease-1>', x=2, y=2); root.update()
            print(f'   無効化中は押せない {cnt["n"] == 1}'); found.configure(state='normal')
        else: print('FlatButton が見つからない')
        total = sum(1 for _ in iter_all(root)); print(f'== 画面の部品 合計 {total}、Button(FlatButton) {sum(isinstance(w, macui.FlatButton) for w in iter_all(root))}')
    finally:
        print('== 例外', len(errors), '件'); [print(e) for e in errors[:5]]
        root.after(100, root.destroy)
def iter_all(w):
    for c in w.winfo_children():
        yield c; yield from iter_all(c)
root.after(5000, run); root.mainloop()
