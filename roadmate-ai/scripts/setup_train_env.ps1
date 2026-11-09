# 학습 환경
param([string]$Python = "3.11", [string]$Cuda = "cu126", [string]$Torch = "2.14.1")
. "$PSScriptRoot\_common.ps1"
$venv = Join-Path $Root ".venv-train"
$tp = Join-Path $venv "Scripts\python.exe"

New-Venv $venv $Python
& $tp -m pip install "torch==$Torch" --index-url "https://download.pytorch.org/whl/$Cuda"
if ($LASTEXITCODE -ne 0) { throw "torch install failed" }
& $tp -m pip install -r (Join-Path $Root "requirements-train.txt")
if ($LASTEXITCODE -ne 0) { throw "train requirements install failed" }
& $tp -c "import torch, transformers, peft, trl, bitsandbytes; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '-'); print('bf16', torch.cuda.is_available() and torch.cuda.is_bf16_supported())"
