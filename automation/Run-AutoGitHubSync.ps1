param([switch]$Publish)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = (Get-Command py -ErrorAction Stop).Source
$Arguments = @('-3', (Join-Path $PSScriptRoot 'auto_github_sync.py'))
if ($Publish) { $Arguments += '--publish' }
& $Python @Arguments
exit $LASTEXITCODE
