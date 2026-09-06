# Регистрирует задачу в планировщике Windows, которая ищет новые видео
# в папке Google Drive и заливает их на YouTube.
#
#   .\setup_task.ps1                          - каждые 30 мин с 10:00 до 00:00
#   .\setup_task.ps1 -From 08:00 -To 23:00    - своё окно
#   .\setup_task.ps1 -Every 60                - раз в час внутри окна
#   .\setup_task.ps1 -At 21:00                - один раз в сутки, без окна
#   .\setup_task.ps1 -Remove                  - удалить задачу

param(
    [string]$From = "10:00",
    [string]$To = "00:00",
    [int]$Every = 30,
    [string]$At = "",
    [string]$TaskName = "ClaudeYouTubeUploader",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Задача '$TaskName' удалена."
    exit 0
}

$runScript = Join-Path $PSScriptRoot "run.ps1"
if (-not (Test-Path $runScript)) { throw "Не найден run.ps1 рядом со скриптом." }

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runScript`"" `
    -WorkingDirectory $PSScriptRoot

if ($At) {
    # Один раз в сутки.
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    $when = "ежедневно в $At"
} else {
    # Окно: старт в $From, повтор каждые $Every минут до $To.
    $start = [datetime]::ParseExact($From, "HH:mm", $null)
    $end = [datetime]::ParseExact($To, "HH:mm", $null)
    if ($end -le $start) { $end = $end.AddDays(1) }   # 00:00 значит следующей ночью
    $window = $end - $start
    if ($window.TotalMinutes -lt $Every) { throw "Окно короче интервала повтора." }

    $trigger = New-ScheduledTaskTrigger -Daily -At $start
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $start `
        -RepetitionInterval (New-TimeSpan -Minutes $Every) `
        -RepetitionDuration $window).Repetition

    $when = "ежедневно с $From до $To, каждые $Every мин"
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Берёт видео из папки Google Drive, генерирует метаданные локальной моделью и заливает на YouTube" | Out-Null

Write-Host "Задача '$TaskName' создана: $when."
Write-Host "Проверить вручную:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Лог:                $(Join-Path $PSScriptRoot 'logs\uploader.log')"
