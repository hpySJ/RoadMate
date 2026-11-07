# AI 서버(FastAPI) 실행. -Mock이면 모델 없이 계약 시험용 응답
param([switch]$Background, [switch]$Mock, [int]$Port = 8000)
. "$PSScriptRoot\_common.ps1"
if (-not (Test-Path $Py)) { throw ".venv missing: run scripts\setup_ai_env.ps1" }
# .env는 config.py가 읽는다(서버·smoke·평가 공통)
$env:MODEL_BACKEND = if ($Mock) { "mock" } else { "llama" }
$env:PYTHONIOENCODING = "utf-8"

# 백엔드 연동: 백엔드(8080)는 http://127.0.0.1:8000 으로 호출, 시연은 reload 없이 worker 1개
$a = @("-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", $Port, "--workers", "1")
if ($Background) {
    $p = Start-Bg "ai" $Py $a $Port
    Wait-Http "http://127.0.0.1:$Port/health/live" 60 $p
} else {
    & $Py @a
}
