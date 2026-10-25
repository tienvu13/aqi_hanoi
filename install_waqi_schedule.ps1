$ErrorActionPreference = "Stop"

$projectDir = $PSScriptRoot
$tokenFile = Join-Path $projectDir "waqi_token.txt"
$batchFile = Join-Path $projectDir "run_waqi.bat"
$taskName = "AQI Hanoi WAQI Collection"

if (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) {
    throw "Khong tim thay waqi_token.txt trong thu muc du an."
}

if (-not (Test-Path -LiteralPath $batchFile -PathType Leaf)) {
    throw "Khong tim thay run_waqi.bat trong thu muc du an."
}

$python = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Khong tim thay .venv\Scripts\python.exe. Hay tao moi truong .venv truoc."
}

$now = Get-Date
$firstRun = $now.Date.AddHours($now.Hour + 1)
$user = "$env:USERDOMAIN\$env:USERNAME"
$taskCommand = "cmd.exe /d /c `"$batchFile`""
$arguments = @(
    "/Create",
    "/SC", "HOURLY",
    "/MO", "1",
    "/ST", $firstRun.ToString("HH:mm"),
    "/SD", $firstRun.ToString("MM/dd/yyyy", [Globalization.CultureInfo]::InvariantCulture),
    "/TN", $taskName,
    "/TR", $taskCommand,
    "/RU", $user,
    "/IT",
    "/F"
)

& schtasks.exe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Khong tao duoc tac vu Task Scheduler (ma loi $LASTEXITCODE)."
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if ($task.State -eq "Disabled" -or $task.Actions.Arguments -notlike "*$batchFile*") {
    throw "Tac vu da tao nhung cau hinh kich hoat/duong dan khong dung."
}

$info = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction Stop
Write-Host "Da tao tac vu '$taskName'."
Write-Host "Lan chay ke tiep: $($info.NextRunTime)"
Write-Host "Tac vu chay khi ban dang dang nhap Windows; log: raw\log.txt"
