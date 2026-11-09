# 종료
. "$PSScriptRoot\_common.ps1"
foreach ($name in @("ai", "llama")) {
    $pidFile = Join-Path $Root "run\$name.pid"
    if (Test-Path $pidFile) {
        $p = Get-Bg $name
        if ($p) { Stop-Process -Id $p.Id -Force; Write-Host "stopped $name (pid $($p.Id))" }
        else { Write-Host "$name not running (stale pid file removed)" }
        Remove-Item $pidFile
    }
}
