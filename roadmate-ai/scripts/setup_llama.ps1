# 모델 런타임
param(
    [string]$Tag = "",
    [ValidateSet("cuda-12.4", "cuda-13.4", "vulkan", "cpu")][string]$Backend = ""
)
. "$PSScriptRoot\_common.ps1"
$m = Read-Manifest
if (-not $Tag) { $Tag = $m.runtime.llama_cpp_tag }
if (-not $Backend) { $Backend = $m.runtime.llama_cpp_backend }

$dest = Join-Path $Root "tools\llama"
$src = Join-Path $Root "tools\llama-src"
$tmp = Join-Path $env:TEMP "roadmate-llama-$Tag"
New-Item -ItemType Directory -Force $dest, $tmp | Out-Null

$base = "https://github.com/ggml-org/llama.cpp/releases/download/$Tag"
$assets = @("llama-$Tag-bin-win-$Backend-x64.zip")

if ($Backend -like "cuda-*") { $assets += "cudart-llama-bin-win-$Backend-x64.zip" }

$hashes = [ordered]@{}
foreach ($a in $assets) {
    $zip = Join-Path $tmp $a
    if (-not (Test-Path $zip)) {
        Write-Host "download $a"
        Invoke-WebRequest "$base/$a" -OutFile $zip -UseBasicParsing
    }
    $hashes[$a] = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower()
    Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
}

$srcZip = Join-Path $tmp "src-$Tag.zip"
if (-not (Test-Path $srcZip)) {
    Write-Host "download source $Tag"
    Invoke-WebRequest "https://github.com/ggml-org/llama.cpp/archive/refs/tags/$Tag.zip" -OutFile $srcZip -UseBasicParsing
}
if (Test-Path $src) { Remove-Item -LiteralPath $src -Recurse -Force }
Expand-Archive -LiteralPath $srcZip -DestinationPath $tmp -Force
Move-Item -LiteralPath (Join-Path $tmp "llama.cpp-$Tag") -Destination $src

[ordered]@{ tag = $Tag; backend = $Backend; sha256 = $hashes } | ConvertTo-Json |
    Set-Content -Path (Join-Path $dest "VERSION.json") -Encoding utf8
& (Join-Path $dest "llama-server.exe") --version
Write-Host "llama.cpp $Tag ($Backend) installed in tools\llama"
