# Registers the netmon scheduled tasks on the collector host. Run in an elevated PowerShell.
# Review before running: it creates three tasks that run as the given service account.
#
#   .\install-tasks.ps1 -ServiceAccount 'CAMPUS\svc-netmon' -AppDir 'C:\netmon\app\collector' `
#       -Python 'C:\netmon\venv\Scripts\python.exe' -Config 'C:\netmon\config.toml'
#
# Tasks:
#   netmon-full-run   08:00 and 16:30 daily  -> python -m netmon run
#   netmon-reach      every 15 minutes       -> python -m netmon reach
#   netmon-api        at startup, restarts   -> python -m netmon serve
param(
  [Parameter(Mandatory)] [string] $ServiceAccount,
  [string] $AppDir = 'C:\netmon\app\collector',
  [string] $Python = 'C:\netmon\venv\Scripts\python.exe',
  [string] $Config = 'C:\netmon\config.toml'
)
$ErrorActionPreference = 'Stop'
foreach ($p in @($AppDir, $Python, $Config)) { if (-not (Test-Path $p)) { throw "Not found: $p" } }

# The tasks run as the service account whether or not anyone is logged on, which needs the
# "Log on as a batch job" right. Without it Windows silently never starts them (LastTaskResult
# 267011, "has not yet run"), as seen on the first install. This only reads the policy.
function Test-HolderList([string[]] $Holders, [string] $Sid, [string] $Account) {
  $short = ($Account -split '\\')[-1]
  foreach ($h in $Holders) {
    $h = $h.Trim().TrimStart('*')
    if ($h -and ($h -eq $Sid -or $h -eq $Account -or $h -eq $short)) { return $true }
  }
  return $false
}

function Test-BatchLogonRight([string] $Account) {
  # $true / $false, or $null when it cannot be determined.
  try { $sid = (New-Object Security.Principal.NTAccount($Account)).Translate([Security.Principal.SecurityIdentifier]).Value } catch { return $null }
  $cfg = Join-Path $env:TEMP "netmon-rights-$PID.cfg"
  try {
    & secedit /export /areas USER_RIGHTS /cfg $cfg | Out-Null
    if (-not (Test-Path $cfg)) { return $null }
    $line = (Select-String -Path $cfg -Pattern '^SeBatchLogonRight\s*=' | Select-Object -First 1).Line
  } finally { Remove-Item $cfg -ErrorAction SilentlyContinue }
  if (-not $line) { return $false }
  $holders = ($line -split '=', 2)[1] -split ','
  if (Test-HolderList $holders $sid $Account) { return $true }
  foreach ($h in $holders) {  # the right may come through a built-in local group
    $g = $h.Trim().TrimStart('*')
    if ($g -like 'S-1-5-32-*') {
      try { if (Get-LocalGroupMember -SID $g -ErrorAction Stop | Where-Object { $_.SID.Value -eq $sid }) { return $true } } catch {}
    }
  }
  return $false
}

$batchRightHint = @"
  1. In this Administrator window run:  secpol.msc
  2. Local Policies > User Rights Assignment > double-click 'Log on as a batch job' > Add User or Group...
  3. If the PC is in a domain, click Locations... and choose this computer, then type the account name > Check Names > OK
  4. Click OK on the 'Log on as a batch job Properties' window (it is not applied until you do)
  If a domain Group Policy controls this right, ask the domain admin to add $ServiceAccount there instead.
"@

switch (Test-BatchLogonRight $ServiceAccount) {
  $true  { Write-Host "OK: $ServiceAccount has 'Log on as a batch job'." -ForegroundColor Green }
  $false { Write-Warning "$ServiceAccount does NOT have 'Log on as a batch job', so Windows will not start the tasks. Fix it first:`n$batchRightHint" }
  default { Write-Warning "Could not check 'Log on as a batch job' for $ServiceAccount (run this window as Administrator). The check after registration will tell." }
}

$cred = Get-Credential -UserName $ServiceAccount -Message 'Password for the netmon service account (stored by Task Scheduler)'
$pw = $cred.GetNetworkCredential().Password

function New-NetmonAction([string] $verb) {
  New-ScheduledTaskAction -Execute $Python -Argument "-m netmon --config `"$Config`" $verb" -WorkingDirectory $AppDir
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$triggers = @((New-ScheduledTaskTrigger -Daily -At '08:00'), (New-ScheduledTaskTrigger -Daily -At '16:30'))
Register-ScheduledTask -TaskName 'netmon-full-run' -Action (New-NetmonAction 'run') -Trigger $triggers -Settings $settings `
  -User $ServiceAccount -Password $pw -RunLevel Limited -Description 'Read-only campus switch/WLC discovery (LLD workflow)' -Force | Out-Null

$reachTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15)
$reachSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName 'netmon-reach' -Action (New-NetmonAction 'reach') -Trigger $reachTrigger -Settings $reachSettings `
  -User $ServiceAccount -Password $pw -RunLevel Limited -Description 'TCP/22 up/down check of known switches every 15 minutes' -Force | Out-Null

$apiSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'netmon-api' -Action (New-NetmonAction 'serve') -Trigger (New-ScheduledTaskTrigger -AtStartup) -Settings $apiSettings `
  -User $ServiceAccount -Password $pw -RunLevel Limited -Description 'Campus-only HTTPS API for the hub Network Monitoring page' -Force | Out-Null

Write-Host 'Registered netmon-full-run, netmon-reach and netmon-api.'

# Proof that Windows really launches tasks as this account: start the API and look at LastRunTime.
# A task that never ran keeps LastRunTime in 1999 and LastTaskResult 267011.
Start-ScheduledTask -TaskName 'netmon-api'
Start-Sleep -Seconds 8
$info = Get-ScheduledTaskInfo -TaskName 'netmon-api'
if ($info.LastRunTime.Year -lt 2000) {
  Write-Warning "Windows did not start netmon-api (LastTaskResult $($info.LastTaskResult)). Almost always the missing 'Log on as a batch job' right:`n$batchRightHint`nThen run:  schtasks /run /tn netmon-api"
} elseif ($info.LastTaskResult -eq 267009) {
  Write-Host 'netmon-api is running. Check it with:  curl.exe -k https://localhost:8443/api/v1/health' -ForegroundColor Green
} else {
  Write-Warning "netmon-api started but stopped (LastTaskResult $($info.LastTaskResult)). See C:\netmon\data\logs\netmon-serve.log, or run 'C:\netmon\netmon serve' in the svc-netmon window to see the error."
}
