[CmdletBinding()]
param([string]$PostgresBin = "$env:TEMP\wifiguard-pg16-test-tools\pgsql\bin", [switch]$Stop, [switch]$BuildUI)
$ErrorActionPreference = 'Stop'
$repoRoot = (Get-Item -LiteralPath $PSScriptRoot).Parent.Parent.Parent.FullName
$localRoot = Join-Path $repoRoot 'WIFIGUARD-LOCAL-EDGE'
$pythonPath = Join-Path $localRoot '.venv\Scripts\python.exe'
$statePath = Join-Path $localRoot 'local-output\pc-pi5-demo'
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Local Edge Python environment is missing. See deploy/pc/README.md.' }
if ($Stop) {
    Push-Location $localRoot
    try { & $pythonPath -m wifiguard_local_edge.pc_demo --stop; if ($LASTEXITCODE -ne 0) { throw 'Stop request failed.' } }
    finally { Pop-Location }
    $instancePath = Join-Path $statePath 'demo-instance.json'
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        if (!(Test-Path -LiteralPath $instancePath)) { return }
        try {
            $instance = Get-Content -LiteralPath $instancePath -Raw | ConvertFrom-Json
            if (!$instance.running) {
                Write-Host 'PC fixture stopped. Test DB and history are retained.'
                return
            }
        } catch { }
        Start-Sleep -Seconds 1
    }
    throw 'Stop has not completed yet. Check the fixture logs before restarting.'
}
if ($BuildUI) {
    Push-Location (Join-Path $repoRoot 'WIFIGUARD-FRONTEND')
    try { & node scripts/build-pi5.mjs --pc; if ($LASTEXITCODE -ne 0) { throw 'PC UI build failed.' } }
    finally { Pop-Location }
}
if (!(Test-Path -LiteralPath (Join-Path $PostgresBin 'pg_ctl.exe'))) { throw 'Native PostgreSQL binaries are missing. Pass -PostgresBin with the bin directory.' }
$null = New-Item -ItemType Directory -Path $statePath -Force
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$manager = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'wifiguard_local_edge.pc_demo', '--postgres-bin', ('"' + $PostgresBin + '"')) -WorkingDirectory $localRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $statePath "$stamp-launch.log") -RedirectStandardError (Join-Path $statePath "$stamp-launch-error.log")
Write-Host 'Starting the isolated PC fixture. Existing operating databases are not used.'
for ($attempt = 0; $attempt -lt 45; $attempt++) {
    if ($manager.HasExited) { throw "PC fixture exited. Read $statePath\$stamp-launch-error.log" }
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:8095/health' -TimeoutSec 2
        Write-Host 'Open http://127.0.0.1:8095 and click the PC test-account auto-fill button.'
        Write-Host 'Then click start reception and begin the 60-second empty-room calibration.'
        Write-Host 'Stop: run this same script with -Stop. Test DB and history are retained.'
        return
    } catch { Start-Sleep -Seconds 1 }
}
throw "Fixture startup timed out. Read the logs in $statePath. Use -Stop before retrying."
