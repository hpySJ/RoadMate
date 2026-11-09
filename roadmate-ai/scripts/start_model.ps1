# QWEN 구현: llama-server로 선택 프로필 Q4/Q8(+선택 LoRA) 실행
param([switch]$Background, [int]$GpuLayers = -1)
. "$PSScriptRoot\_common.ps1"
$m = Read-Manifest
$r = $m.runtime
$exe = Join-Path $Root "tools\llama\llama-server.exe"
$model = Join-Path $Root ("models\" + $m.model.gguf_file)
if (-not (Test-Path $exe)) { throw "tools\llama\llama-server.exe missing: run scripts\setup_llama.ps1" }
if (-not (Test-Path $model)) { throw "model missing: run .venv\Scripts\python.exe scripts\download_model.py" }
if (-not (Test-Path $Py)) { throw ".venv missing: run scripts\setup_ai_env.ps1" }
# 큰 모델 해시는 프로세스 시작과 Wait-Http 제한 시간 전에 검증한다.
& $Py (Join-Path $Root "runtime_identity.py")
if ($LASTEXITCODE -ne 0) { throw "model runtime preparation failed" }
$ngl = if ($GpuLayers -ge 0) { $GpuLayers } else { $r.n_gpu_layers }

# 파라미터 튜닝: 문맥은 manifest, 슬롯 1, 출력 상한 256, GPU 레이어, 배치, RAM 프롬프트 캐시 끔
$a = @(
    "-m", $model, "--alias", $m.model.alias,
    "-c", $r.ctx, "-np", $r.parallel, "-n", $r.n_predict,
    "-ngl", $ngl, "-b", $r.batch, "-ub", $r.ubatch, "--cache-ram", $r.cache_ram_mib,
    "--jinja", "--host", "127.0.0.1", "--port", $r.port
)
if ($m.adapter) {
    $adapterFile = if ([System.IO.Path]::IsPathRooted($m.adapter.file)) { $m.adapter.file } else { Join-Path $Root $m.adapter.file }
    $a += @("--lora", $adapterFile)
}

$p = Start-Bg "llama" $exe $a $r.port
Wait-Http "http://127.0.0.1:$($r.port)/health" 300 $p
if (-not $Background) {
    try { Wait-Process -Id $p.Id } finally {
        if (-not $p.HasExited) { Stop-Process -Id $p.Id }
    }
}
