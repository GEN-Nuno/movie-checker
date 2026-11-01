#!/bin/bash
# 映画チェッカー を Mac 用に固める: venv を作り直し → 固定した依存 → (pip-audit) → pytest → icns → PyInstaller (.app) → dmg / zip
# 使い方: bash packaging/build_mac.sh [--skip-audit] [--skip-tests] [--python /path/to/python3.13]
# 版数の正本は app/moviecheck/version.py (Windows と同じ)。同じ版の dmg が dist にあれば止まる (上げ忘れ防止)。
# 出来る物: dist/映画チェッカー-<版>-mac-<arch>.dmg と .zip。中身は dist/mac/映画チェッカー.app。
# 署名していないので初回は「開発元を確認できません」が出る (右クリック → 開く)。Developer ID で署名・公証すれば消える。
set -euo pipefail

SKIP_AUDIT=0; SKIP_TESTS=0; PYTHON=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-audit) SKIP_AUDIT=1;;
    --skip-tests) SKIP_TESTS=1;;
    --python) PYTHON="$2"; shift;;
    *) echo "不明な引数: $1" >&2; exit 2;;
  esac; shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/app"
VENV="$ROOT/.venv-build-mac"
DIST="$ROOT/dist"
WORK="$ROOT/build"
T0=$(date +%s)
unset ELECTRON_RUN_AS_NODE 2>/dev/null || true

# --- 0/6 版数と道具 ---
VERSION=$(sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' "$APP/moviecheck/version.py" | head -1)
[ -n "$VERSION" ] || { echo "app/moviecheck/version.py に __version__ がありません" >&2; exit 1; }
ARCH=$(uname -m)
DMG="$DIST/映画チェッカー-$VERSION-mac-$ARCH.dmg"
ZIP="$DIST/映画チェッカー-$VERSION-mac-$ARCH.zip"
echo "== 0/6 映画チェッカー $VERSION ($ARCH)"
if [ -e "$DMG" ]; then echo "同じ版の dmg が既にあります: $DMG"; echo "app/moviecheck/version.py の版数を上げてください" >&2; exit 1; fi
if [ -z "$PYTHON" ]; then
  for c in /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13 python3.13; do
    if command -v "$c" >/dev/null 2>&1; then PYTHON="$c"; break; fi
  done
fi
[ -n "$PYTHON" ] || { echo "Python 3.13 がありません:  brew install python@3.13 python-tk@3.13" >&2; exit 1; }
"$PYTHON" -c "import tkinter" 2>/dev/null || { echo "tkinter が入っていません:  brew install python-tk@3.13" >&2; exit 1; }

# --- 1/6 venv ---
echo "== 1/6 venv を作り直す ($VENV) with $PYTHON"
rm -rf "$VENV"
"$PYTHON" -m venv "$VENV"
PY="$VENV/bin/python"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$APP/requirements.txt" -r "$APP/requirements-dev.txt"
PYVER=$("$PY" -c "import sys, ssl; print(sys.version.split()[0] + ' / ' + ssl.OPENSSL_VERSION)")
echo "   Python / OpenSSL: $PYVER"
case "$PYVER" in *"OpenSSL 3."*) ;; *) echo "OpenSSL 3 系ではありません ($PYVER)" >&2; exit 1;; esac

# --- 2/6 pip-audit ---
echo "== 2/6 pip-audit (依存の脆弱性)"
if [ "$SKIP_AUDIT" = 1 ]; then
  echo "   監査していない (--skip-audit)。オフラインの時だけ使う"
else
  "$PY" -m pip_audit --progress-spinner off -r "$APP/requirements.txt" || { echo "pip-audit が通りませんでした (指摘があるか、監査そのものが失敗。上の出力を見る)" >&2; exit 1; }
fi

# --- 3/6 pytest ---
echo "== 3/6 pytest"
if [ "$SKIP_TESTS" = 1 ]; then
  echo "   試験していない (--skip-tests)"
else
  (cd "$APP" && "$PY" -m pytest -q) || { echo "pytest が落ちました" >&2; exit 1; }
fi

# --- 4/6 アイコンと build_info ---
echo "== 4/6 icns と build_info.json"
rm -rf "$DIST/mac" "$WORK/mac" "$WORK/movie_icon.iconset"
mkdir -p "$WORK" "$DIST"
"$PY" - "$APP/movie_icon.ico" "$WORK/movie_icon.iconset" <<'PY'
import sys
from pathlib import Path
from PIL import Image
src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
im = Image.open(src)
# .ico の中で一番大きい絵を元にする
best = max(getattr(im, "ico", None).sizes() if hasattr(im, "ico") else [im.size], key=lambda s: s[0])
im.size = best
im = im.convert("RGBA")
for n in (16, 32, 128, 256, 512):
    im.resize((n, n), Image.LANCZOS).save(out / f"icon_{n}x{n}.png")
    im.resize((n * 2, n * 2), Image.LANCZOS).save(out / f"icon_{n}x{n}@2x.png")
print("   元の絵:", best)
PY
iconutil -c icns "$WORK/movie_icon.iconset" -o "$WORK/movie_icon.icns"
SSL=$("$PY" -c "import ssl; print(ssl.OPENSSL_VERSION)")
AUDITED=$([ "$SKIP_AUDIT" = 1 ] && echo false || echo true)
TESTED=$([ "$SKIP_TESTS" = 1 ] && echo false || echo true)
"$PY" - "$WORK/build_info.json" "$VERSION" "$PYVER" "$SSL" "$AUDITED" "$TESTED" <<'PY'
import json, sys, datetime
p, version, pyver, ssl, audited, tested = sys.argv[1:]
info = {"version": version, "built": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "python": pyver,
        "openssl": ssl, "audited": audited == "true", "tested": tested == "true", "platform": "mac"}
open(p, "w", encoding="utf-8").write(json.dumps(info, ensure_ascii=False, indent=2))
PY

# --- 5/6 PyInstaller ---
echo "== 5/6 PyInstaller (.app)"
"$PY" -m PyInstaller --noconfirm --clean --distpath "$DIST/mac" --workpath "$WORK/mac" "$ROOT/packaging/movie_mac.spec"
APPB="$DIST/mac/映画チェッカー.app"
[ -d "$APPB" ] || { echo ".app が無い: $APPB" >&2; exit 1; }
rm -rf "$DIST/mac/映画チェッカー"     # onedir の生フォルダは .app に入っているので消す
# 署名 (ad-hoc)。Apple Silicon は署名が無いと起動を拒まれるので最低限これを付ける
codesign --force --deep --sign - "$APPB" 2>/dev/null || echo "   codesign (ad-hoc) に失敗。そのまま続ける"

# --- 6/6 dmg / zip ---
echo "== 6/6 dmg と zip"
STAGE="$WORK/dmg"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APPB" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG" "$ZIP"
hdiutil create -quiet -volname "映画チェッカー $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
(cd "$DIST/mac" && ditto -c -k --sequesterRsrc --keepParent "映画チェッカー.app" "$ZIP")
rm -rf "$STAGE"

# --- 記録 ---
SIZE_DMG=$(stat -f %z "$DMG"); SIZE_ZIP=$(stat -f %z "$ZIP")
SIZE_APP=$(du -sk "$APPB" | cut -f1)
ELAPSED=$(( $(date +%s) - T0 ))
"$PY" - "$DIST/VERSION-mac.json" "$WORK/build_info.json" "$DMG" "$SIZE_DMG" "$ZIP" "$SIZE_ZIP" "$SIZE_APP" "$ELAPSED" <<'PY'
import json, sys
out, info_p, dmg, sdmg, zp, szip, sapp, el = sys.argv[1:]
info = json.load(open(info_p, encoding="utf-8"))
info.update({"dmg": dmg, "dmg_bytes": int(sdmg), "zip": zp, "zip_bytes": int(szip), "app_kbytes": int(sapp), "elapsed_sec": int(el)})
open(out, "w", encoding="utf-8").write(json.dumps(info, ensure_ascii=False, indent=2))
PY
printf '   dmg  %s  %s bytes\n' "$DMG" "$SIZE_DMG"
printf '   zip  %s  %s bytes\n' "$ZIP" "$SIZE_ZIP"
printf '   app  %s KB  %s  (%s 秒)\n' "$SIZE_APP" "$SSL" "$ELAPSED"
