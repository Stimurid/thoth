# Install Thoth as a daily Windows Scheduled Task.
# Collects all local agent chats (codex, claude-code, cursor) incrementally.

param(
    [string]$ArchiveRoot,
    [string]$RepoRoot,
    [string]$DailyAt = '03:33',
    [string]$TaskName = 'ThothDaily',
    [switch]$RunNow
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot)    { $RepoRoot    = (Resolve-Path (Join-Path $scriptDir '..')).Path }
if (-not $ArchiveRoot) { $ArchiveRoot = Join-Path $RepoRoot 'archive' }

$srcDir = Join-Path $RepoRoot 'src'
foreach ($p in @($srcDir, (Join-Path $srcDir 'thoth\__main__.py'))) {
    if (-not (Test-Path $p)) { Write-Error "Missing prerequisite: $p"; exit 2 }
}

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { Write-Error "python not on PATH."; exit 4 }
$pyExe = $py.Source

New-Item -ItemType Directory -Force -Path $ArchiveRoot | Out-Null
$logDir = Join-Path $ArchiveRoot '_logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$cmdPath = Join-Path $logDir 'thoth_daily.cmd'
$outLog  = Join-Path $logDir 'thoth_daily.out.log'
$errLog  = Join-Path $logDir 'thoth_daily.err.log'

$cmdBody = @"
@echo off
setlocal
cd /d "$RepoRoot"
set "PYTHONPATH=$srcDir"
set "PYTHONUNBUFFERED=1"
echo [%DATE% %TIME%] starting Thoth daily >> "$outLog"
"$pyExe" -u -m thoth all --output "$ArchiveRoot" --json >> "$outLog" 2>> "$errLog"
set EC=%ERRORLEVEL%
echo [%DATE% %TIME%] Thoth daily exit code: %EC% >> "$outLog"
endlocal
"@
Set-Content -Path $cmdPath -Value $cmdBody -Encoding ASCII -NoNewline

try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null } catch {}

$action    = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c call `"$cmdPath`"" -WorkingDirectory $RepoRoot
$trigger   = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings  = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "[OK] registered scheduled task '$TaskName' daily at $DailyAt"
Write-Host "     output archive : $ArchiveRoot"
Write-Host "     cmd            : $cmdPath"
Write-Host "     stdout log     : $outLog"
Write-Host "     next run       : $((Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)"

if ($RunNow) {
    Write-Host "`n[RunNow] starting the task once now..."
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 5
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "     last run time   : $($info.LastRunTime)"
    Write-Host "     last task result: $($info.LastTaskResult)"
    Write-Host "     tail $outLog to watch progress"
}
