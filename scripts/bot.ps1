param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("start", "stop", "status")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$stateDirectory = Join-Path $projectRoot ".bot"
$pidFile = Join-Path $stateDirectory "telegram-bot.pid"
$stdoutLog = Join-Path $stateDirectory "telegram-bot.stdout.log"
$stderrLog = Join-Path $stateDirectory "telegram-bot.stderr.log"

function Get-ProjectBotProcesses {
    $expectedPython = [IO.Path]::GetFullPath($python)
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match "^python(w)?\.exe$" -and
        $_.ExecutablePath -and
        [string]::Equals(
            [IO.Path]::GetFullPath($_.ExecutablePath),
            $expectedPython,
            [StringComparison]::OrdinalIgnoreCase
        ) -and
        [string]$_.CommandLine -match "(?i)(?:^|\s)-m\s+src\.transport\.telegram_bot(?:\s|$)"
    }
}

if (($Action -eq "start" -or $Action -eq "status") -and
    -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python was not found at '$python'. Create the project .venv first."
}

switch ($Action) {
    "start" {
        New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
        $running = @(Get-ProjectBotProcesses)
        if ($running.Count -gt 0) {
            Set-Content -LiteralPath $pidFile -Value $running[0].ProcessId -Encoding Ascii
            Write-Output "Bot already running (PID $($running[0].ProcessId)); no duplicate started."
            break
        }

        $process = Start-Process `
            -FilePath $python `
            -ArgumentList "-m src.transport.telegram_bot" `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -PassThru
        Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding Ascii
        Start-Sleep -Seconds 3

        $running = @(Get-ProjectBotProcesses | Where-Object { $_.ProcessId -eq $process.Id })
        if ($running.Count -eq 0) {
            Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
            Write-Output "Bot exited during startup. Recent error output:"
            if (Test-Path -LiteralPath $stderrLog) {
                Get-Content -LiteralPath $stderrLog -Tail 30
            }
            throw "Telegram bot did not remain running. See '$stderrLog'."
        }

        Write-Output "Bot started (PID $($process.Id))."
    }

    "stop" {
        $running = @(Get-ProjectBotProcesses)
        if ($running.Count -eq 0) {
            Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
            Write-Output "Bot is not running."
            break
        }

        foreach ($botProcess in $running) {
            Stop-Process -Id $botProcess.ProcessId
        }

        $deadline = (Get-Date).AddSeconds(5)
        do {
            Start-Sleep -Milliseconds 200
            $remaining = @(Get-ProjectBotProcesses)
        } while ($remaining.Count -gt 0 -and (Get-Date) -lt $deadline)

        foreach ($botProcess in $remaining) {
            Stop-Process -Id $botProcess.ProcessId -Force
        }

        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        Write-Output "Bot stopped."
    }

    "status" {
        $running = @(Get-ProjectBotProcesses)
        if ($running.Count -eq 0) {
            Write-Output "Bot is not running."
        } else {
            $running | ForEach-Object {
                Write-Output "Bot is running (PID $($_.ProcessId))."
            }
        }
    }
}
