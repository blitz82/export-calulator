param([string]$Python = "python", [string]$WebViewRuntime = "build/WebView2", [switch]$Lite,
      [string]$OutputRoot = "release", [switch]$Preflight)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$appName = "공동정산 수출실적 관리"
$distPath = Join-Path $OutputRoot "desktop"
$variant = "portable"
$zipSuffix = "Portable"
$guide = "docs/WINDOWS_PORTABLE.txt"
if ($Lite) {
    $distPath = Join-Path $OutputRoot "lite-desktop"
    $variant = "lite"
    $zipSuffix = "Lite"
    $guide = "docs/WINDOWS_LITE.txt"
} else {
    $runtime = (Resolve-Path -LiteralPath $WebViewRuntime).Path
    $runtimeExe = Join-Path $runtime "msedgewebview2.exe"
    if (!(Test-Path -LiteralPath $runtimeExe -PathType Leaf)) { throw "WebView2 x64 고정 런타임의 압축을 먼저 풀어 주세요." }
    $signature = Get-AuthenticodeSignature -LiteralPath $runtimeExe
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "O=Microsoft Corporation") {
        throw "Microsoft WebView2 실행 파일 서명을 확인할 수 없습니다."
    }
}
$packageTarget = [System.IO.Path]::GetFullPath((Join-Path $distPath $appName))
$allowedOutput = [System.IO.Path]::GetFullPath($OutputRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (!$packageTarget.StartsWith($allowedOutput, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "패키지 대상이 지정한 출력 폴더 밖입니다."
}
if (Test-Path -LiteralPath $packageTarget -PathType Container) {
    $hasDataFolder = Test-Path -LiteralPath (Join-Path $packageTarget 'data')
    $existingDatabase = Get-ChildItem -LiteralPath $packageTarget -Recurse -File | Where-Object { $_.Name -match '\.(db|sqlite|sqlite3)(-wal|-shm)?$' } | Select-Object -First 1
    if ($hasDataFolder -or $existingDatabase) {
        throw "기존 사용자 데이터가 있는 패키지 폴더는 덮어쓰지 않습니다. 새 -OutputRoot를 지정하고 기존 자료를 보존하세요."
    }
}
& $Python -c "import webview; import clr; import webview.platforms.winforms"
if ($LASTEXITCODE -ne 0) { throw "데스크톱 빌드 의존성을 불러올 수 없습니다. 지정한 Python 환경에 requirements-dev.txt를 설치하세요." }
Get-Command npm.cmd -ErrorAction Stop | Out-Null
& $Python -m epms.packaging_checks $variant --webview $WebViewRuntime
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($Preflight) { return }
npm.cmd run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m epms.prepare_template
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m PyInstaller --noconfirm --clean --workpath (Join-Path $OutputRoot "work/$variant") --distpath $distPath EPMS.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (!$Lite) {
    Copy-Item -LiteralPath $runtime -Destination "$distPath/$appName/WebView2" -Recurse
}
Copy-Item -LiteralPath $guide -Destination "$distPath/$appName/실행 안내.txt"
$packageDir = Join-Path $distPath $appName
Copy-Item -LiteralPath "Start-EPMS-Sorting-Review.cmd" -Destination $packageDir
Copy-Item -LiteralPath "Start-EPMS-Offline-Browser.cmd" -Destination $packageDir
if ($Lite) {
    Copy-Item -LiteralPath "Start-EPMS-OCR.cmd" -Destination $packageDir
} else {
    Rename-Item -LiteralPath (Join-Path $packageDir "$appName.exe") -NewName "EPMS.exe"
    Copy-Item -LiteralPath "Start-EPMS-Portable.cmd" -Destination (Join-Path $packageDir "공동정산 수출실적 관리 시작.cmd")
    Copy-Item -LiteralPath "Start-EPMS-Portable.cmd" -Destination $packageDir
}
Copy-Item -LiteralPath "docs/OCR_REVIEW_WORKFLOW.md" -Destination "$packageDir/OCR 안내.md"
Copy-Item -LiteralPath "docs/OCR_TEMPLATE_GUIDE.md" -Destination $packageDir
Copy-Item -LiteralPath "docs/SETTLEMENT_WORKFLOW.md" -Destination $packageDir
Copy-Item -LiteralPath "docs/SUPPORT_BUDGET_WORKFLOW.md" -Destination $packageDir
Copy-Item -LiteralPath "docs/MONTHLY_REPORT_WORKFLOW.md" -Destination $packageDir
Copy-Item -LiteralPath "docs/MONTHLY_WORKFLOW_GUIDE.md" -Destination $packageDir
if (Test-Path -LiteralPath "docs/sorting" -PathType Container) {
    Copy-Item -LiteralPath "docs/sorting" -Destination (Join-Path $packageDir "sorting-guide") -Recurse -Force
}
Copy-Item -LiteralPath "docs/OPEN_SOURCE_COMPLIANCE.md" -Destination "$distPath/$appName/오픈소스 검토 안내.md"
Copy-Item -LiteralPath "docs/RELEASE_2_0_0.md" -Destination "$packageDir/RELEASE_NOTES.md"
& $Python -m epms.packaging_checks $variant --webview $WebViewRuntime --output (Join-Path $packageDir "build-info.json")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m epms.oss_inventory "$distPath/$appName" $variant --analysis (Join-Path $OutputRoot "work/$variant/EPMS/Analysis-00.toc")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$version = (Get-Content -LiteralPath package.json -Raw -Encoding UTF8 | ConvertFrom-Json).version
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "package.json의 릴리스 버전을 확인하세요." }
$zip = Join-Path $OutputRoot "EPMS-Windows-x64-$zipSuffix-v$version.zip"
& $Python -m zipfile -c $zip "$distPath/$appName"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
(Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() | Set-Content -Encoding ascii "$zip.sha256"
& $Python -m epms.supply_chain verify-zip $zip $variant (Join-Path $packageDir "sbom/epms-$variant.cdx.json") --require-build-info
exit $LASTEXITCODE
