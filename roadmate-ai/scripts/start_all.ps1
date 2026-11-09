# 실행
param([switch]$Mock)
. "$PSScriptRoot\_common.ps1"
if (-not $Mock) { & "$PSScriptRoot\start_model.ps1" -Background }
& "$PSScriptRoot\start_ai.ps1" -Background -Mock:$Mock
& $Py (Join-Path $Root "scripts\smoke.py")
if ($LASTEXITCODE -ne 0) { throw "smoke test failed (see logs\)" }
