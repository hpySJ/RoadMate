# 공통: 경로, manifest, 백그라운드 실행, 준비 대기
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path $PSScriptRoot -Parent
$Py = Join-Path $Root ".venv\Scripts\python.exe"

function Read-Manifest {
    $manifestPython = if (Test-Path -LiteralPath $Py) { $Py }
        elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" }
        elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" }
        else { throw "Python not found. Run scripts\setup_ai_env.ps1 first." }
    $raw = & $manifestPython -c "import sys; sys.path.insert(0, sys.argv[1]); import json; from config import load_manifest; print(json.dumps(load_manifest()))" $Root
    if ($LASTEXITCODE -ne 0) { throw "MODEL_PROFILE manifest could not be loaded" }
    $raw | ConvertFrom-Json
}

function New-Venv([string]$Path, [string]$Version) {
    if (Test-Path (Join-Path $Path "Scripts\python.exe")) { return }
    if (Get-Command py -ErrorAction SilentlyContinue) { & py "-$Version" -m venv $Path }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv $Path }
    else { throw "Python $Version not found. Install Python $Version (python.org) first." }
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed: $Path" }
    & (Join-Path $Path "Scripts\python.exe") -m pip install --upgrade pip
}

function Get-Bg([string]$Name) {
    # 검증: PID 파일의 PID와 시작 시각이 모두 같은 프로세스만 우리 프로세스로 본다(PID 재사용 대비)
    $pidFile = Join-Path $Root "run\$Name.pid"
    if (-not (Test-Path $pidFile)) { return $null }
    $id, $ticks = "$(Get-Content $pidFile -Raw)".Trim() -split "\s+"
    $p = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
    try {
        if ($p -and $ticks -and [math]::Abs($p.StartTime.ToUniversalTime().Ticks - [long]$ticks) -lt 10000000) { return $p }
    } catch { }
    return $null
}

function Start-Bg([string]$Name, [string]$Exe, [object[]]$ArgList, [int]$Port) {
    $old = Get-Bg $Name
    if ($old) { throw "$Name already running (pid $($old.Id)). Run scripts\stop_all.ps1 first." }
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "port $Port is already in use by another process. Stop it first (netstat -ano | findstr :$Port)."
    }
    $logs = Join-Path $Root "logs"; $run = Join-Path $Root "run"
    New-Item -ItemType Directory -Force $logs, $run | Out-Null
    $quoted = ($ArgList | ForEach-Object {
            $s = "$_"; if ($s -match '[\s"]') { '"' + ($s -replace '"', '\"') + '"' } else { $s }
        }) -join " "
    $p = Start-Process -FilePath $Exe -ArgumentList $quoted -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs "$Name.out.log") -RedirectStandardError (Join-Path $logs "$Name.err.log")
    Set-Content -Path (Join-Path $run "$Name.pid") -Value "$($p.Id) $($p.StartTime.ToUniversalTime().Ticks)" -Encoding ascii
    Write-Host "$Name started (pid $($p.Id)), logs: logs\$Name.*.log"
    return $p
}

function Wait-Http([string]$Url, [int]$TimeoutSec, $Process) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        # 검증: 새 프로세스가 죽었으면 다른 프로세스의 응답을 준비 완료로 보지 않는다
        if ($Process -and $Process.HasExited) { throw "process exited before ready: $Url (see logs\)" }
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { Write-Host "ready: $Url"; return }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "timeout waiting for $Url (see logs\)"
}
