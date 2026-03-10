param(
    [string]$ExePath = "C:/Program Files (x86)/Steam/steamapps/common/Slay the Spire 2/SlayTheSpire2.exe",
    [int]$Attempts = 40,
    [int]$DelaySeconds = 2,
    [switch]$EnableDebugActions
)

$ErrorActionPreference = "Stop"

function Wait-ForHealth {
    param(
        [int]$MaxAttempts,
        [int]$SleepSeconds,
        [System.Diagnostics.Process]$Process
    )

    for ($i = 0; $i -lt $MaxAttempts; $i++) {
        Start-Sleep -Seconds $SleepSeconds

        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:8080/health" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                return
            }
        } catch {
        }

        if ($Process.HasExited) {
            throw "Game process exited before /health became ready."
        }
    }

    throw "Timed out waiting for /health."
}

$existing = Get-Process -Name "SlayTheSpire2" -ErrorAction SilentlyContinue
if ($existing) {
    Stop-Process -Id $existing.Id -Force
    Start-Sleep -Seconds 2
}

$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $ExePath
$startInfo.UseShellExecute = $false

if ($EnableDebugActions) {
    $startInfo.EnvironmentVariables["STS2_ENABLE_DEBUG_ACTIONS"] = "1"
} else {
    $startInfo.EnvironmentVariables.Remove("STS2_ENABLE_DEBUG_ACTIONS")
}

$proc = [System.Diagnostics.Process]::Start($startInfo)
Wait-ForHealth -MaxAttempts $Attempts -SleepSeconds $DelaySeconds -Process $proc

[pscustomobject]@{
    pid = $proc.Id
    debug_actions_enabled = [bool]$EnableDebugActions
    health = "ready"
} | ConvertTo-Json -Compress
