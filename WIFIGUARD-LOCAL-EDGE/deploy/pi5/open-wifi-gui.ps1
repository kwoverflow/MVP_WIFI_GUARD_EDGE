param(
  [Parameter(Mandatory=$true)][string]$PiAddress,
  [Parameter(Mandatory=$true)][string]$IdentityFile,
  [string]$BindAddress,
  [ValidateRange(1024,65535)][int]$LocalPort = 18080
)
$ErrorActionPreference = 'Stop'
if ($PiAddress -notmatch '^[a-zA-Z0-9.:-]+$') { throw 'Invalid Pi address' }
$keyPath = (Resolve-Path -LiteralPath $IdentityFile).Path
# Foreground tunnel deliberately lets the operator see authentication/host-key
# failures. Keep this terminal open; closing it closes the encrypted GUI path.
$sshArgs = @('-N', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
  '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
  '-o', 'KexAlgorithms=curve25519-sha256', '-o', 'HostKeyAlgorithms=ssh-ed25519',
  '-i', $keyPath, '-L', "127.0.0.1:${LocalPort}:127.0.0.1:8080")
if ($BindAddress) { $sshArgs += @('-b', $BindAddress) }
$sshArgs += "wifiguard@$PiAddress"
Write-Host "Keep this window open. Once SSH connects, open http://127.0.0.1:$LocalPort/devices in your browser."
& ssh.exe @sshArgs
if ($LASTEXITCODE -ne 0) { throw 'Secure GUI tunnel was not established. Do not enter a password on an ordinary LAN HTTP page.' }
