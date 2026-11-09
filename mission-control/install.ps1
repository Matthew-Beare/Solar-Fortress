$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pi = 'root@192.168.1.21'
$manifest = Get-Content -LiteralPath (Join-Path $root 'manifest.json') -Raw | ConvertFrom-Json
$htmlPath = Join-Path $root 'Solar_Fortress_Mission_Control_Live.html'
$verifyPath = Join-Path $root 'verify_render.html'
$hash = (Get-FileHash -LiteralPath $htmlPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne $manifest.html_sha256) { throw 'HTML checksum mismatch. Nothing uploaded.' }
if ((Get-FileHash -LiteralPath $verifyPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.verifier_sha256) {
    throw 'Render verifier checksum mismatch. Nothing uploaded.'
}
$programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
$browserCandidates = @(
    "$programFilesX86\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$programFilesX86\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$browser = $browserCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (!$browser) { throw 'Edge or Chrome is required for rendered UI checks. Nothing uploaded. Open verify_render.html in Firefox to see the test report.' }
$work = Join-Path ([IO.Path]::GetTempPath()) ('sf-ui-v028-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$stdout = Join-Path $work 'render.html'
$stderr = Join-Path $work 'renderer.log'
$browserProfile = Join-Path $work 'browser-profile'
$reportPath = Join-Path $root 'render-verification-result.html'
$oldLocation = Get-Location
try {
    Set-Location -LiteralPath $root
    Write-Host '=== OFFLINE PACKAGE INTEGRITY CHECKS ===' -ForegroundColor Cyan
    & python -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Offline checks failed. Nothing uploaded.' }
    Write-Host '=== RENDERED COLOURS, BLINK INDICATIONS AND LABEL FIT ===' -ForegroundColor Cyan
    $uri = ([Uri]$verifyPath).AbsoluteUri
    $arguments = @('--headless=new', '--disable-gpu', '--no-first-run',
        '--no-default-browser-check', '--disable-background-networking',
        "--user-data-dir=$browserProfile", '--dump-dom', '--virtual-time-budget=90000',
        '--window-size=1280,900', $uri)
    # Start-Process joins ArgumentList; explicitly quote each Windows argument.
    $quoted = $arguments | ForEach-Object { '"' + $_ + '"' }
    $startOptions = @{
        FilePath = $browser
        ArgumentList = $quoted
        PassThru = $true
        RedirectStandardOutput = $stdout
        RedirectStandardError = $stderr
    }
    $process = Start-Process @startOptions
    if (!$process.WaitForExit(45000)) {
        $process.Kill()
        throw "Browser rendering checks timed out. Nothing uploaded. Log: $stderr"
    }
    $process.WaitForExit()
    if (!(Test-Path -LiteralPath $stdout)) { throw 'Browser produced no test report. Nothing uploaded.' }
    Copy-Item -LiteralPath $stdout -Destination $reportPath -Force
    $rendered = [IO.File]::ReadAllText($stdout)
    if ($rendered -notmatch 'data-sf-render-verification="pass"') {
        throw "Rendered UI checks FAILED or incomplete. Nothing uploaded. Open: $reportPath"
    }
    if ($rendered -notmatch ('data-sf-source-sha256="' + $hash + '"')) {
        throw 'Browser checked a different HTML source. Nothing uploaded.'
    }
    Write-Host "RENDERED UI CHECKS PASSED. Report: $reportPath" -ForegroundColor Green
    $archive = Join-Path $work 'payload.zip'
    Compress-Archive -LiteralPath @(
        $htmlPath, (Join-Path $root 'deploy_pi.py'), (Join-Path $root 'manifest.json')
    ) -DestinationPath $archive -Force
    $nonce = [guid]::NewGuid().ToString('N')
    $remoteZip = "/data/solar-fortress/.sf-ui-v028-$nonce.zip"
    $remoteDir = "/data/solar-fortress/.sf-ui-v028-$nonce"
    Write-Host '=== UPLOADING TESTED UI ===' -ForegroundColor Cyan
    & scp -o ConnectTimeout=15 $archive ($pi + ':' + $remoteZip)
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed.' }
    Write-Host '=== UI INSTALLATION / NO SERVICE RESTART ===' -ForegroundColor Cyan
    & ssh -o ConnectTimeout=15 $pi "python3 -m zipfile -e $remoteZip $remoteDir && python3 $remoteDir/deploy_pi.py"
    if ($LASTEXITCODE -ne 0) { throw 'Pi UI installation FAILED. Read the output above.' }
    Write-Host 'SOLAR FORTRESS UI v0.2.8 INSTALLER PASSED' -ForegroundColor Green
} finally {
    Set-Location -LiteralPath $oldLocation
}
