# 모델
param([switch]$Background, [int]$GpuLayers = -1)
. "$PSScriptRoot\_common.ps1"
$m = Read-Manifest
$r = $m.runtime
$exe = Join-Path $Root "tools\llama\llama-server.exe"
$model = Join-Path $Root ("models\" + $m.model.gguf_file)
if (-not (Test-Path $exe)) { throw "tools\llama\llama-server.exe missing: run scripts\setup_llama.ps1" }
if (-not (Test-Path $model)) { throw "model missing: run .venv\Scripts\python.exe scripts\download_model.py" }
if (-not (Test-Path $Py)) { throw ".venv missing: run scripts\setup_ai_env.ps1" }

& $Py (Join-Path $Root "runtime_identity.py")
if ($LASTEXITCODE -ne 0) { throw "model runtime preparation failed" }
$ngl = if ($GpuLayers -ge 0) { $GpuLayers } else { $r.n_gpu_layers }


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
