# Обёртка для планировщика задач Windows: запускает один проход загрузки.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

# Ключ Claude: берём из user-переменной окружения, если задана,
# иначе из файла .env рядом со скриптом (строка ANTHROPIC_API_KEY=...).
if (-not $env:ANTHROPIC_API_KEY) {
    $envFile = Join-Path $PSScriptRoot ".env"
    if (Test-Path $envFile) {
        Get-Content $envFile | ForEach-Object {
            if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
                Set-Item -Path ("env:" + $matches[1]) -Value $matches[2].Trim('"')
            }
        }
    }
}

& $python uploader.py run @args
exit $LASTEXITCODE
