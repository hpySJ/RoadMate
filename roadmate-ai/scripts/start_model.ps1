# QWEN 구현: llama-server로 Qwen3-4B-Instruct-2507 Q4_K_M(+선택 LoRA) 실행
param([switch]$Background, [int]$GpuLayers = -1)
. "$PSScriptRoot\_common.ps1"
$m = Read-Manifest
$r = $m.runtime
$exe = Join-Path $Root "tools\llama\llama-server.exe"
$model = Join-Path $Root ("models\" + $m.model.gguf_file)
if (-not (Test-Path $exe)) { throw "tools\llama\llama-server.exe missing: run scripts\setup_llama.ps1" }
if (-not (Test-Path $model)) { throw "model missing: run .venv\Scripts\python.exe scripts\download_model.py" }
$ngl = if ($GpuLayers -ge 0) { $GpuLayers } else { $r.n_gpu_layers }

# 파라미터 튜닝: 문맥 2048, 슬롯 1, 출력 상한 256, GPU 레이어, 배치, RAM 프롬프트 캐시 끔
$a = @(
    "-m", $model, "--alias", $m.model.alias,
    "-c", $r.ctx, "-np", $r.parallel, "-n", $r.n_predict,
    "-ngl", $ngl, "-b", $r.batch, "-ub", $r.ubatch, "--cache-ram", $r.cache_ram_mib,
    "--jinja", "--host", "127.0.0.1", "--port", $r.port
)
if ($m.adapter) { $a += @("--lora", (Join-Path $Root $m.adapter.file)) }

if ($Background) {
    Start-Bg "llama" $exe $a
    Wait-Http "http://127.0.0.1:$($r.port)/health" 300
} else {
    & $exe @a
}
