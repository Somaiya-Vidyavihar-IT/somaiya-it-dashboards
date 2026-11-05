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

Write-Host 'Registered netmon-full-run, netmon-reach and netmon-api. Start the API now with: Start-ScheduledTask -TaskName netmon-api'
