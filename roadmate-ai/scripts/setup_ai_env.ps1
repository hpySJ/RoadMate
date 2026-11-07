# AI 서버 Python 환경(.venv) 구성: lock 파일 기준 설치
param([string]$Python = "3.11")
. "$PSScriptRoot\_common.ps1"

New-Venv (Join-Path $Root ".venv") $Python
& $Py -m pip install -r (Join-Path $Root "requirements.lock.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
& $Py -c "import fastapi, httpx, huggingface_hub; print('ok fastapi', fastapi.__version__)"
