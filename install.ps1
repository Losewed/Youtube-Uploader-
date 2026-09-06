# Ставит зависимости в локальное виртуальное окружение .venv
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Find-Python {
    # Пробуем по очереди: лаунчер с конкретными версиями, затем просто python.
    $candidates = @(
        @{ Exe = "py";     Args = @("-3.13") },
        @{ Exe = "py";     Args = @("-3.12") },
        @{ Exe = "py";     Args = @("-3") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $version = & $c.Exe @($c.Args + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")) 2>$null
        } catch { continue }
        if (-not $version) { continue }
        $parts = $version.Trim().Split(".")
        if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 12) {
            Write-Host "Python $($version.Trim()) - подходит ($($c.Exe) $($c.Args -join ' '))"
            return $c
        }
        Write-Host "Python $($version.Trim()) - слишком старый, нужен 3.12+"
    }
    return $null
}

if (-not (Test-Path ".venv")) {
    $py = Find-Python
    if ($null -eq $py) {
        Write-Host ""
        Write-Host "Не нашёл Python 3.12 или новее." -ForegroundColor Red
        Write-Host "Скачайте с https://www.python.org/downloads/ и при установке"
        Write-Host "обязательно отметьте галочку 'Add Python to PATH',"
        Write-Host "затем закройте и откройте PowerShell заново."
        Write-Host ""
        Write-Host "Что сейчас видит система:"
        if (Get-Command py -ErrorAction SilentlyContinue) { & py --list } else { Write-Host "  лаунчер py не установлен" }
        exit 1
    }
    Write-Host "Создаю виртуальное окружение..."
    & $py.Exe @($py.Args + @("-m", "venv", ".venv"))
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Окружение не создалось - файл $venvPython отсутствует." -ForegroundColor Red
    exit 1
}

Write-Host "Ставлю зависимости..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "ffmpeg не найден в PATH. Без него не будет ни кадров для модели, ни вертикальной конверсии."
    Write-Host "Поставить: winget install Gyan.FFmpeg   (потом перезапустить PowerShell)"
}

Write-Host ""
Write-Host "Готово. Дальше:"
Write-Host "  .\.venv\Scripts\python.exe uploader.py check    - проверить всю цепочку"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\setup_task.ps1 -At 21:00"
