# 공통: 경로, manifest, 백그라운드 실행, 준비 대기
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path $PSScriptRoot -Parent
$Py = Join-Path $Root ".venv\Scripts\python.exe"

function Read-Manifest {
    Get-Content -Raw -Encoding UTF8 (Join-Path $Root "models\manifest.json") | ConvertFrom-Json
}

function New-Venv([string]$Path, [string]$Version) {
    if (Test-Path (Join-Path $Path "Scripts\python.exe")) { return }
    if (Get-Command py -ErrorAction SilentlyContinue) { & py "-$Version" -m venv $Path }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv $Path }
    else { throw "Python $Version not found. Install Python $Version (python.org) first." }
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed: $Path" }
    & (Join-Path $Path "Scripts\python.exe") -m pip install --upgrade pip
}

function Start-Bg([string]$Name, [string]$Exe, [object[]]$ArgList) {
    $logs = Join-Path $Root "logs"; $run = Join-Path $Root "run"
    New-Item -ItemType Directory -Force $logs, $run | Out-Null
    $quoted = ($ArgList | ForEach-Object {
            $s = "$_"; if ($s -match '[\s"]') { '"' + ($s -replace '"', '\"') + '"' } else { $s }
        }) -join " "
    $p = Start-Process -FilePath $Exe -ArgumentList $quoted -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs "$Name.out.log") -RedirectStandardError (Join-Path $logs "$Name.err.log")
    Set-Content -Path (Join-Path $run "$Name.pid") -Value $p.Id -Encoding ascii
    Write-Host "$Name started (pid $($p.Id)), logs: logs\$Name.*.log"
}

function Wait-Http([string]$Url, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { Write-Host "ready: $Url"; return }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "timeout waiting for $Url (see logs\)"
}
