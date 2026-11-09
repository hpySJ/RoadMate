# 테스트
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell startup integration")
@pytest.mark.parametrize("launcher,mock,hash_exit", [
    ("start_model.ps1", False, 0), ("start_model.ps1", False, 7),
    ("start_ai.ps1", False, 0), ("start_ai.ps1", False, 7), ("start_ai.ps1", True, 7),
])
def test_windows_hash_preparation_precedes_launch_and_wait(tmp_path, launcher, mock, hash_exit):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / "scripts" / launcher, scripts / launcher)
    (tmp_path / "tools/llama").mkdir(parents=True)
    (tmp_path / "tools/llama/llama-server.exe").touch()
    (tmp_path / "models").mkdir()
    (tmp_path / "models/synthetic.gguf").write_bytes(b"synthetic")
    (scripts / "_common.ps1").write_text('''
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Py = Join-Path $Root "fake_python.ps1"
function Read-Manifest {
    return [pscustomobject]@{ model = [pscustomobject]@{ gguf_file = "synthetic.gguf"; alias = "synthetic" };
        runtime = [pscustomobject]@{ n_gpu_layers = 99; ctx = 4096; parallel = 1; n_predict = 256;
            batch = 256; ubatch = 128; cache_ram_mib = 0; port = 8081 } }
}
function Start-Bg([string]$Name, [string]$Exe, [object[]]$ArgList, [int]$Port) {
    Add-Content -LiteralPath (Join-Path $Root "trace.txt") -Value ("start " + $Name)
    return [pscustomobject]@{ Id = 1 }
}
function Wait-Http([string]$Url, [int]$TimeoutSec, $Process) {
    Add-Content -LiteralPath (Join-Path $Root "trace.txt") -Value "wait"
}
''', encoding="utf-8")
    (tmp_path / "fake_python.ps1").write_text('''
param([string]$Target)
if ((Split-Path $Target -Leaf) -ne "runtime_identity.py") { throw "unexpected Python command" }
Add-Content -LiteralPath (Join-Path $PSScriptRoot "trace.txt") -Value "hash"
''' + f"$global:LASTEXITCODE = {hash_exit}\n", encoding="utf-8")
    cmd = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
           str(scripts / launcher), "-Background"]
    if mock:
        cmd.append("-Mock")
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=30)
    trace = (tmp_path / "trace.txt").read_text(encoding="utf-8-sig").splitlines()
    if hash_exit and not mock:
        assert result.returncode != 0
        assert trace == ["hash"]
    else:
        assert result.returncode == 0, result.stderr
        name = "llama" if launcher == "start_model.ps1" else "ai"
        assert trace == ([] if mock else ["hash"]) + [f"start {name}", "wait"]
