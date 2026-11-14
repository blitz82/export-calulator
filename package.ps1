param([string]$Python = "python", [string]$WebViewRuntime = "build/WebView2")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$appName = "공동정산 수출실적 관리"
$runtime = (Resolve-Path -LiteralPath $WebViewRuntime).Path
$runtimeExe = Join-Path $runtime "msedgewebview2.exe"
if (!(Test-Path -LiteralPath $runtimeExe -PathType Leaf)) { throw "WebView2 x64 고정 런타임의 압축을 먼저 풀어 주세요." }
$signature = Get-AuthenticodeSignature -LiteralPath $runtimeExe
if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "O=Microsoft Corporation") {
    throw "Microsoft WebView2 실행 파일 서명을 확인할 수 없습니다."
}
& $Python -c "import webview; import clr; import webview.platforms.winforms"
if ($LASTEXITCODE -ne 0) { throw "데스크톱 빌드 의존성을 불러올 수 없습니다. 지정한 Python 환경에 requirements-dev.txt를 설치하세요." }
npm.cmd run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m epms.prepare_template
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m PyInstaller --noconfirm --clean --distpath release/desktop EPMS.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item -LiteralPath $runtime -Destination "release/desktop/$appName/WebView2" -Recurse
Copy-Item -LiteralPath "docs/WINDOWS_PORTABLE.txt" -Destination "release/desktop/$appName/실행 안내.txt"
& $Python -m zipfile -c "release/$appName-Windows-x64-Portable.zip" "release/desktop/$appName"
exit $LASTEXITCODE
