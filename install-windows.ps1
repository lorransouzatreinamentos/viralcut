# VIRALCUT — instalador Windows (DaVinci Resolve Studio)
# Uso: abra o PowerShell na pasta do projeto e rode:
#   powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
$ErrorActionPreference = "Stop"
Write-Host "== VIRALCUT — instalacao ==" -ForegroundColor Cyan

function Need($cmd, $hint) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    Write-Host "FALTA: $cmd" -ForegroundColor Red
    Write-Host "  $hint" -ForegroundColor Yellow
    exit 1
  }
}

# 1. Pre-requisitos
Need python "Instale Python 3.11+ em https://python.org (marque 'Add Python to PATH')."
Need git    "Instale o Git em https://git-scm.com."
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "AVISO: ffmpeg nao encontrado. Instale com:  winget install Gyan.FFmpeg" -ForegroundColor Yellow
  Write-Host "  (depois feche e reabra o PowerShell)"
}

# 2. Ambiente Python
Write-Host "Criando ambiente e instalando dependencias..."
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv\Scripts\pip.exe install --quiet -r requirements.txt

# 3. Chave da OpenAI -> %USERPROFILE%\.viralcut\.env
$key = Read-Host "Cole a chave da OpenAI (sk-...)"
$vcdir = Join-Path $env:USERPROFILE ".viralcut"
New-Item -ItemType Directory -Force -Path $vcdir | Out-Null
$envPath = Join-Path $vcdir ".env"
$content = "OPENAI_API_KEY=$key`r`nVIRALCUT_LLM=openai`r`nVIRALCUT_LLM_MODEL=gpt-4o`r`n"
[System.IO.File]::WriteAllText($envPath, $content, (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "OK! Instalado." -ForegroundColor Green
Write-Host "Para usar:"
Write-Host "  1. No DaVinci Resolve: Preferences > System > General >" -ForegroundColor White
Write-Host "     'External scripting using' = Local" -ForegroundColor White
Write-Host "  2. Abra o Resolve com uma timeline, entao rode:  .\viralcut.bat" -ForegroundColor White
