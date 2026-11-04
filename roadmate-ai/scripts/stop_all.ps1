# 종료: AI 서버 → 모델 서버 순서
. "$PSScriptRoot\_common.ps1"
foreach ($name in @("ai", "llama")) {
    $pidFile = Join-Path $Root "run\$name.pid"
    if (Test-Path $pidFile) {
        $id = [int](Get-Content $pidFile)
        try { Stop-Process -Id $id -Force -ErrorAction Stop; Write-Host "stopped $name (pid $id)" } catch { Write-Host "$name not running" }
        Remove-Item $pidFile
    }
}
