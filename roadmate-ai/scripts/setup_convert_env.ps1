# 학습: LoRA → GGUF 변환 환경(.venv-convert). llama.cpp 같은 태그의 변환기 요구사항을 그대로 사용
param([string]$Python = "3.11")
. "$PSScriptRoot\_common.ps1"
$src = Join-Path $Root "tools\llama-src"
if (-not (Test-Path (Join-Path $src "convert_lora_to_gguf.py"))) { throw "tools\llama-src missing: run scripts\setup_llama.ps1" }
$venv = Join-Path $Root ".venv-convert"
$cp = Join-Path $venv "Scripts\python.exe"

New-Venv $venv $Python
& $cp -m pip install -r (Join-Path $src "requirements\requirements-convert_hf_to_gguf.txt")
if ($LASTEXITCODE -ne 0) { throw "converter requirements install failed" }
& $cp -m pip install -e (Join-Path $src "gguf-py") huggingface_hub
if ($LASTEXITCODE -ne 0) { throw "gguf-py install failed" }
& $cp (Join-Path $src "convert_lora_to_gguf.py") --help | Select-Object -First 3
