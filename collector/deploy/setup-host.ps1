# One-time (and re-runnable) setup of the netmon collector on the campus VM.
# Run in an elevated PowerShell from this folder (collector\deploy of the downloaded repository):
#
#   powershell -ExecutionPolicy Bypass -File .\setup-host.ps1 -ServiceAccount '<VM_NAME>\svc-netmon'
#
# It installs into C:\netmon:  app\collector (code), venv (Python packages), config.toml (only created
# if missing - never overwritten), tls\, data\, and netmon.cmd (so commands are just "C:\netmon\netmon ...").
# It does not touch any switch, WLC or Windows setting outside C:\netmon.
param(
  [string] $ServiceAccount = '',
  [string] $Root = 'C:\netmon'
)
$ErrorActionPreference = 'Stop'
$src = Split-Path -Parent $PSScriptRoot

Write-Host '1/5 Checking Python 3.13...'
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  throw "Python is not installed. Install Python 3.13 (64-bit) from https://www.python.org/downloads/windows/ - tick 'Use admin privileges' and 'Add python.exe to PATH' - then run this script again."
}
& py -3.13 --version
if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 was not found by the py launcher. Install Python 3.13 (64-bit), then run this script again.' }

Write-Host "2/5 Copying the collector to $Root\app\collector..."
New-Item -ItemType Directory -Force "$Root\app", "$Root\tls", "$Root\data" | Out-Null
robocopy $src "$Root\app\collector" /MIR /XD __pycache__ .venv venv data /XF config.toml *.pem *.pfx *.key /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copying the collector failed (robocopy exit code $LASTEXITCODE)." }

Write-Host '3/5 Installing Python packages (needs internet, or set HTTPS_PROXY first)...'
if (-not (Test-Path "$Root\venv\Scripts\python.exe")) { & py -3.13 -m venv "$Root\venv" }
& "$Root\venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$Root\venv\Scripts\python.exe" -m pip install --quiet -r "$Root\app\collector\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. If the VM reaches the internet through a proxy, run: $env:HTTPS_PROXY = "http://<PROXY>:<PORT>" and try again.' }

Write-Host '4/5 Creating the netmon command and config...'
$cmd = "@echo off`r`npushd `"$Root\app\collector`"`r`n`"$Root\venv\Scripts\python.exe`" -m netmon --config `"$Root\config.toml`" %*`r`nset rc=%errorlevel%`r`npopd`r`nexit /b %rc%`r`n"
Set-Content -Path "$Root\netmon.cmd" -Value $cmd -Encoding Ascii -NoNewline
$newConfig = -not (Test-Path "$Root\config.toml")
if ($newConfig) { Copy-Item "$src\config.example.toml" "$Root\config.toml" }

Write-Host '5/5 Restricting C:\netmon (it will hold the certificate key and network data)...'
if ($ServiceAccount) {
  & icacls $Root /inheritance:r /grant:r 'Administrators:(OI)(CI)F' 'SYSTEM:(OI)(CI)F' "${ServiceAccount}:(OI)(CI)M" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not set permissions for $ServiceAccount. Check the account name (e.g. $env:COMPUTERNAME\svc-netmon)." }
} else {
  Write-Warning 'No -ServiceAccount given: permissions not restricted. Re-run with -ServiceAccount once the account exists.'
}

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
if ($newConfig) { Write-Host "Next: edit $Root\config.toml (or replace it with the prepared config), then follow the setup guide." }
Write-Host "Check the installation any time with:  $Root\netmon check-config"
