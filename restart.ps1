# Restart the Agency Session Dashboard (port 8420)
$ErrorActionPreference = "Stop"
$Port = 8420
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Stop-OnPort {
    param([switch]$Force)
    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    if ($connections) {
        $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($pid in $pids) {
            try {
                if ($Force) {
                    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                } else {
                    Stop-Process -Id $pid -ErrorAction SilentlyContinue
                }
            } catch { }
        }
    }
}

function Wait-ForFree {
    for ($i = 0; $i -lt 10; $i++) {
        $conn = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        if (-not $conn) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

Stop-OnPort
if (-not (Wait-ForFree)) {
    Write-Host "... port $Port still in use, forcing shutdown"
    Stop-OnPort -Force
    if (-not (Wait-ForFree)) {
        Write-Host "x Could not free port $Port"
        exit 1
    }
}

# Start the server
Set-Location $Dir
& "$Dir\.venv\Scripts\Activate.ps1"
Start-Process -NoNewWindow -FilePath "python" -ArgumentList "app.py" -RedirectStandardOutput "$env:TEMP\dashboard.log" -RedirectStandardError "$env:TEMP\dashboard-err.log"

# Health check
for ($i = 0; $i -lt 10; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            Write-Host "Dashboard running at http://127.0.0.1:$Port/"
            exit 0
        }
    } catch { }
    Start-Sleep -Seconds 1
}

Write-Host "x Failed to start - check $env:TEMP\dashboard.log"
exit 1
