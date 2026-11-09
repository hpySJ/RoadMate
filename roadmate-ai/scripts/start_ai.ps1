# AI 서버
param([switch]$Background, [switch]$Mock, [int]$Port = 8000)
. "$PSScriptRoot\_common.ps1"
if (-not (Test-Path $Py)) { throw ".venv missing: run scripts\setup_ai_env.ps1" }

$env:MODEL_BACKEND = if ($Mock) { "mock" } else { "llama" }
$env:PYTHONIOENCODING = "utf-8"
if (-not $Mock) {
    & $Py (Join-Path $Root "runtime_identity.py")
    if ($LASTEXITCODE -ne 0) { throw "model runtime preparation failed" }
}


$a = @("-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", $Port, "--workers", "1")
if ($Background) {
    $p = Start-Bg "ai" $Py $a $Port
    Wait-Http "http://127.0.0.1:$Port/health/live" 60 $p
} else {
    & $Py @a
}
