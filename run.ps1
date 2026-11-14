param([string]$Python = "python", [int]$Port = 0, [string]$Database, [switch]$NoBrowser, [switch]$Preview)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if ($Preview) {
    if (!$PSBoundParameters.ContainsKey("Python")) {
        $Python = Join-Path $env:USERPROFILE ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"
    }
    if (!(Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python 실행 파일을 찾을 수 없습니다." }
    $env:PYTHONPATH = (Resolve-Path -LiteralPath ".deps-local").Path
    & $Python -c "from fastapi import BackgroundTasks; from epms.api import create_app"
    if ($LASTEXITCODE -ne 0) { throw "개발용 Python 패키지를 불러올 수 없습니다." }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (!$Port) { $Port = 18790 }
    if (!$Database) { $Database = "build/manual-test.db" }
    $NoBrowser = $true
    Write-Host "개발용 주소: http://127.0.0.1:$Port/"
}
if (!(Test-Path "dist/index.html")) { throw "먼저 npm ci와 npm run build를 실행하세요." }
$launchArgs = @("launch.py", "--port", $Port)
if ($Database) { $launchArgs += @("--db", $Database) }
if ($NoBrowser) { $launchArgs += "--no-browser" }
& $Python @launchArgs
exit $LASTEXITCODE
