# 映画チェッカー

eiga.com の上映情報を取り、観たい映画・監督・映画館のウォッチリストと照らし合わせる Windows のデスクトップ アプリ (Python 3.13 + tkinter)。個人で使う物で、商用ではありません。

## できる事

- 上映中の一覧 (人気順 + 公開日順の全作品)。検索、おすすめ順、劇場の窓 (都道府県ごと)。
- ウォッチリスト: 映画・監督 (全作品を自動で取る)・映画館 (館の今の上映作品)。
- おすすめの札: 気分 (おまかせ / 笑いたい / 泣きたい / 迫力 / 静かに) で絞り、観たい / いまいち / 観た の反応から好みを手元で学ぶ (線形バンディット。外には送らない)。
- メール: 新しく上映になった物と毎週のまとめ (SMTP。パスワードは Windows の資格情報に置く)。
- 外観: dark / light。

## 動かす

```
cd app
..\.venv\Scripts\python movie_app.py
```

依存は `app/requirements.txt` (固定)。初回は `python -m venv .venv` と `.venv\Scripts\pip install -r app/requirements.txt -r app/requirements-dev.txt`。

試験: `cd app` で `..\.venv\Scripts\python -m pytest -q`。

固める (setup.exe): `packaging\build.ps1` (版は `app/moviecheck/version.py` だけ上げる)。

## データの置き場 (個人のデータは git に入れない)

| 動かし方 | 置き場 |
|---|---|
| 源 (`movie_app.py`) | `app/` の隣 (`.gitignore` で除外) |
| インストール版 | `%APPDATA%\映画チェッカー` |

置かれる物: ウォッチリスト・設定・好みの学習 (taste.json)・上映中のキャッシュ・写真・通知の記録。どれも eiga.com から取った物と自分の反応だけで、外には送りません。メールのパスワードは json にも源にも書かず、Windows の資格情報 (keyring) に置きます。

## 入れる (配布)

GitHub の Releases から `映画チェッカー-setup-<版>.exe` を落として実行します (インストール先は `%LOCALAPPDATA%\Programs\MovieChecker`、スタート メニューに『映画チェッカー』)。同じ場所に上書きで更新できます。

- 署名していないので、初回は Windows の SmartScreen が止めます。『詳細情報』→『実行』で進めてください。
- 入れた後のデータは `%APPDATA%\映画チェッカー` に出来ます。アンインストールしてもそこは残ります (消すなら手で)。
- 源から動かす・自分で固める手順は上の『動かす』。setup.exe は git には入れず、Releases に付けます。

## 決まり

- 既存の挙動は変えずに足す。会社の情報は扱わない。鍵・パスワードは json や源に書かない。外の AI には送らない。
- eiga.com への要求は個人利用の頻度 (起動時と 6 時間ごと、館と劇場は 1 日キャッシュ)。
