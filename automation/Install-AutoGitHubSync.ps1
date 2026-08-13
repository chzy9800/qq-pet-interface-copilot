param([int]$IntervalHours = 2)

$ErrorActionPreference = 'Stop'
if ($IntervalHours -lt 1) { throw 'IntervalHours must be at least 1.' }
$Runner = Join-Path $PSScriptRoot 'Run-AutoGitHubSync.ps1'
$Command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -Publish"
& schtasks.exe /Create /TN 'PetCatCopilot GitHub Auto Sync' /TR $Command /SC HOURLY /MO $IntervalHours /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not create scheduled task.' }
Write-Output 'Installed task: PetCatCopilot GitHub Auto Sync'
