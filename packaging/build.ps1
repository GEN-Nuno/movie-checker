# 映画チェッカー を固める (MV-3): venv を作り直し → 固定した依存 → pip-audit → pytest → PyInstaller
# 使い方: powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 3.1.0 [-SkipAudit] [-SkipTests] [-Python py.exe の path]
# 罠: requirements*.txt は ASCII だけ (pip-audit が cp932 で読む)。PyInstaller の出力を 2>&1 や *> で回すと INFO が NativeCommandError になって止まる事がある (moviAI で踏んだ)。素のまま流す。
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [switch]$SkipAudit,
    [switch]$SkipTests,
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv-build"
$py = Join-Path $venv "Scripts\python.exe"
$t0 = Get-Date

if (-not $Python) {
    $cand = Get-Command py -ErrorAction SilentlyContinue
    if ($cand) { $Python = "py -3.13" } else { $Python = "python" }
}
Write-Host "== 1/5 venv を作り直す ($venv) with $Python"
if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
if ($Python -like "py *") { & py -3.13 -m venv $venv } else { & $Python -m venv $venv }
if (-not (Test-Path $py)) { throw "venv が作れませんでした: $py" }
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r (Join-Path $root "app\requirements.txt") -r (Join-Path $root "app\requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install に失敗" }
$ver = & $py -c "import sys, ssl; print(sys.version.split()[0] + ' / ' + ssl.OPENSSL_VERSION)"
Write-Host "   Python / OpenSSL: $ver"
if ($ver -notmatch "OpenSSL 3\.") { throw "OpenSSL 3 系ではありません ($ver)。Python 3.13 を使ってください" }

Write-Host "== 2/5 pip-audit (依存の脆弱性)"
if ($SkipAudit) {
    Write-Host "   監査していない (-SkipAudit)。オフラインの時だけ使う" -ForegroundColor Yellow
} else {
    & $py -m pip_audit --progress-spinner off -r (Join-Path $root "app\requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip-audit が通りませんでした (指摘があるか、監査そのものが失敗。上の出力を見る。指摘なら名前と版を控えて札を立てる)" }
}

Write-Host "== 3/5 pytest"
if ($SkipTests) {
    Write-Host "   試験していない (-SkipTests)" -ForegroundColor Yellow
} else {
    Push-Location (Join-Path $root "app")
    try { & $py -m pytest -q; if ($LASTEXITCODE -ne 0) { throw "pytest が落ちました" } } finally { Pop-Location }
}

Write-Host "== 4/5 PyInstaller"
$dist = Join-Path $root "dist"
$work = Join-Path $root "build"
& $py -m PyInstaller --noconfirm --clean --distpath $dist --workpath $work (Join-Path $PSScriptRoot "movie.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller が落ちました" }
$exe = Join-Path $dist "映画チェッカー.exe"
if (-not (Test-Path $exe)) { throw "exe が無い: $exe" }

Write-Host "== 5/5 記録"
$size = (Get-Item $exe).Length
$ssl = & $py -c "import ssl; print(ssl.OPENSSL_VERSION)"
$info = [ordered]@{
    version = $Version; built = (Get-Date).ToString("yyyy-MM-dd HH:mm"); python = $ver; openssl = $ssl
    exe = $exe; bytes = $size; audited = (-not $SkipAudit); tested = (-not $SkipTests)
    elapsed_sec = [int]((Get-Date) - $t0).TotalSeconds
}
$info | ConvertTo-Json | Out-File -Encoding utf8 (Join-Path $dist "VERSION.json")
Write-Host ("   {0}  {1:N0} bytes  {2}  ({3} 秒)" -f $exe, $size, $ssl, $info.elapsed_sec)
