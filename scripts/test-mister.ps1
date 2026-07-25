# Carga .env en la sesión de PowerShell y ejecuta el data engine.
# Uso:  .\scripts\test-mister.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "No existe .env — copia .env.example a .env y rellena los valores." -ForegroundColor Yellow
    Write-Host "  copy .env.example .env"
    exit 1
}

Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $parts = $line -split "=", 2
    if ($parts.Count -ne 2) { return }
    $key = $parts[0].Trim()
    $val = $parts[1].Trim().Trim('"').Trim("'")
    Set-Item -Path "Env:$key" -Value $val
}

Write-Host "MISTER_TOKEN set: $([bool]$env:MISTER_TOKEN)" -ForegroundColor Cyan
Write-Host "MISTER_X_AUTH set: $([bool]$env:MISTER_X_AUTH)" -ForegroundColor Cyan
Write-Host "MISTER_PHPSESSID set: $([bool]$env:MISTER_PHPSESSID)" -ForegroundColor Cyan
Write-Host ""
Write-Host "Ejecutando data engine..." -ForegroundColor Green
py -3 src/data_engine.py
