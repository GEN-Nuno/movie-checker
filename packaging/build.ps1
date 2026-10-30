# 映画チェッカー を固める (MV-3 / MV-4): venv を作り直し → 固定した依存 → pip-audit → pytest → PyInstaller (onedir + onefile) → Inno Setup
# 使い方: powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-SkipAudit] [-SkipTests] [-Python py.exe の path]
# 版数の正本は app\moviecheck\version.py (ここだけ上げる)。同じ版の setup.exe が dist にあれば止まる (上げ忘れ防止)。
# 罠: requirements*.txt は ASCII だけ (pip-audit が cp932 で読む)。PyInstaller の出力を 2>&1 や *> で回すと INFO が NativeCommandError になって止まる事がある (moviAI で踏んだ)。素のまま流す。
param(
    [switch]$SkipAudit,
    [switch]$SkipTests,
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv-build"
$py = Join-Path $venv "Scripts\python.exe"
$t0 = Get-Date

# --- 0/6 版数 (正本は app/moviecheck/version.py) と道具 ---
$verLine = Get-Content (Join-Path $root "app\moviecheck\version.py") | Where-Object { $_ -match '^__version__\s*=\s*"([^"]+)"' } | Select-Object -First 1
if (-not $verLine) { throw "app\moviecheck\version.py に __version__ がありません" }
$Version = $Matches[1]
Write-Host "== 0/6 映画チェッカー $Version" -ForegroundColor Cyan
$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup がありません:  winget install JRSoftware.InnoSetup" }
$dist = Join-Path $root "dist"
$work = Join-Path $root "build"
$setup = Join-Path $dist "映画チェッカー-setup-$Version.exe"
$setupTmp = Join-Path $work "setup\映画チェッカー-setup-$Version.exe"   # Inno は build\setup に出す (MV-15)
if (Test-Path $setup) { throw "同じ版の setup.exe が既にあります: $setup`napp\moviecheck\version.py の版数を上げてください" }

if (-not $Python) {
    $cand = Get-Command py -ErrorAction SilentlyContinue
    if ($cand) { $Python = "py -3.13" } else { $Python = "python" }
}
Write-Host "== 1/6 venv を作り直す ($venv) with $Python"
if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
if ($Python -like "py *") { & py -3.13 -m venv $venv } else { & $Python -m venv $venv }
if (-not (Test-Path $py)) { throw "venv が作れませんでした: $py" }
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r (Join-Path $root "app\requirements.txt") -r (Join-Path $root "app\requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install に失敗" }
$ver = & $py -c "import sys, ssl; print(sys.version.split()[0] + ' / ' + ssl.OPENSSL_VERSION)"
Write-Host "   Python / OpenSSL: $ver"
if ($ver -notmatch "OpenSSL 3\.") { throw "OpenSSL 3 系ではありません ($ver)。Python 3.13 を使ってください" }

Write-Host "== 2/6 pip-audit (依存の脆弱性)"
if ($SkipAudit) {
    Write-Host "   監査していない (-SkipAudit)。オフラインの時だけ使う" -ForegroundColor Yellow
} else {
    & $py -m pip_audit --progress-spinner off -r (Join-Path $root "app\requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip-audit が通りませんでした (指摘があるか、監査そのものが失敗。上の出力を見る。指摘なら名前と版を控えて札を立てる)" }
}

Write-Host "== 3/6 pytest"
if ($SkipTests) {
    Write-Host "   試験していない (-SkipTests)" -ForegroundColor Yellow
} else {
    Push-Location (Join-Path $root "app")
    try { & $py -m pytest -q; if ($LASTEXITCODE -ne 0) { throw "pytest が落ちました" } } finally { Pop-Location }
}

Write-Host "== 4/6 PyInstaller (onedir + onefile)"
foreach ($d in @((Join-Path $dist "映画チェッカー"), $work)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
New-Item -ItemType Directory -Force $work | Out-Null
$ssl = & $py -c "import ssl; print(ssl.OPENSSL_VERSION)"
$info = [ordered]@{ version = $Version; built = (Get-Date).ToString("yyyy-MM-dd HH:mm"); python = $ver; openssl = $ssl; audited = (-not $SkipAudit); tested = (-not $SkipTests) }
[IO.File]::WriteAllText((Join-Path $work "build_info.json"), ($info | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))  # exe に同梱 (ⓘ の『固めた日』)
& $py -m PyInstaller --noconfirm --clean --distpath $dist --workpath $work (Join-Path $PSScriptRoot "movie.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller が落ちました" }
$exeOne = Join-Path $dist "映画チェッカー.exe"
$exeDir = Join-Path $dist "映画チェッカー\映画チェッカー.exe"
if (-not (Test-Path $exeOne)) { throw "onefile の exe が無い: $exeOne" }
if (-not (Test-Path $exeDir)) { throw "onedir の exe が無い: $exeDir" }

Write-Host "== 5/6 Inno Setup"
# 110 (EndUpdateResource) はアンチウイルスとの競合で、数秒待って同じ事をやり直すと通る (moviAI / Master で踏んだ)
foreach ($try in 1..3) {
    & $iscc "/DAppVersion=$Version" "/Q" (Join-Path $PSScriptRoot "installer.iss")
    if ($LASTEXITCODE -eq 0 -and (Test-Path $setupTmp)) { break }
    Write-Host "   Inno Setup が失敗 ($try 回目)。10 秒待ってやり直す" -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $setupTmp)) { throw "Inno Setup が 3 回失敗しました (110 ならアンチウイルスの除外に dist と build を足す)" }
Move-Item -Force $setupTmp $setup

Write-Host "== 6/6 記録"
$sizeOne = (Get-Item $exeOne).Length
$sizeSetup = (Get-Item $setup).Length
$sizeDir = (Get-ChildItem (Join-Path $dist "映画チェッカー") -Recurse -File | Measure-Object Length -Sum).Sum
$info["exe"] = $exeOne; $info["bytes"] = $sizeOne
$info["setup"] = $setup; $info["setup_bytes"] = $sizeSetup; $info["onedir_bytes"] = $sizeDir
$info["elapsed_sec"] = [int]((Get-Date) - $t0).TotalSeconds
[IO.File]::WriteAllText((Join-Path $dist "VERSION.json"), ($info | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
Write-Host ("   setup  {0}  {1:N0} bytes" -f $setup, $sizeSetup)
Write-Host ("   onedir {0:N0} bytes / onefile {1:N0} bytes  {2}  ({3} 秒)" -f $sizeDir, $sizeOne, $ssl, $info.elapsed_sec)
