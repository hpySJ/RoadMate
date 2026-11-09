# 서버 환경
param([string]$Python = "3.11")
. "$PSScriptRoot\_common.ps1"

New-Venv (Join-Path $Root ".venv") $Python
& $Py -m pip install -r (Join-Path $Root "requirements.lock.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
& $Py -c "import fastapi, httpx, huggingface_hub; print('ok fastapi', fastapi.__version__)"
