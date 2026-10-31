"""個人のデータが git に入らない事の守り (MV-21)。

好み (taste.json)・ウォッチリスト・設定・写真・キャッシュは源から動かすと app/ の隣に出来る。
.gitignore がそれを全部覆っている事と、覆われた物が tracked になっていない事を見る。
"""
import fnmatch
import subprocess
from pathlib import Path

from moviecheck import paths

ROOT = Path(__file__).resolve().parents[2]
PERSONAL = set(paths.DATA_FILES) | {"taste.json", "theater_watchlist.json", "theaters_cache.json", "picks_state.json"}


def _ignore_patterns() -> list[str]:
    return [ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]


def _ignored(rel: str, pats: list[str]) -> bool:
    for p in pats:
        if p.endswith("/"):
            if rel.startswith(p) or ("/" + p) in ("/" + rel):
                return True
        elif fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel.rsplit("/", 1)[-1], p):
            return True
    return False


def test_personal_files_are_ignored_in_app_and_data():
    pats = _ignore_patterns()
    for name in PERSONAL:
        assert _ignored(f"app/{name}", pats), f"app/{name} が .gitignore に無い"
        assert _ignored(f"data/{name}", pats), f"data/{name} が .gitignore に無い"
    assert _ignored("app/posters/1.jpg", pats)


def test_no_personal_file_is_tracked():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return                                   # git が無い環境 (固める venv 等) では見ない
    if out.returncode != 0:
        return
    tracked = [ln for ln in out.stdout.splitlines() if ln]
    bad = [t for t in tracked if t.rsplit("/", 1)[-1] in PERSONAL or t.startswith("app/posters/") or t.endswith(".jpg")]
    assert not bad, f"個人のデータが tracked: {bad}"
