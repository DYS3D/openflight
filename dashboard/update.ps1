# Update the dashboard: pull the latest code, stop every copy serving the
# dashboard port, then start the scheduled task once.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File <repo>\dashboard\update.ps1
param([int]$Port = 8090, [string]$Task = "OpenFlight Dashboard")

$repo = Split-Path -Parent $PSScriptRoot
git -C $repo pull
if ($LASTEXITCODE -ne 0) { Write-Error "git pull failed; dashboard left running"; exit 1 }

# Only processes listening on the dashboard port are stopped.
do {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    $listeners | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    if ($listeners) { Start-Sleep 2 }
} while ($listeners)

Start-ScheduledTask $Task
Start-Sleep 10
$running = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($running) {
    "Dashboard updated to $(git -C $repo log --oneline -1) and running on port $Port"
} else {
    Write-Error "Dashboard did not start; check the '$Task' scheduled task"
    exit 1
}
