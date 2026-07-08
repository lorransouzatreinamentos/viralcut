# =====================================================================
#  VIRALCUT - instalador completo (Windows)
#  Suporta DaVinci Resolve Studio E Adobe Premiere Pro no mesmo instalador.
#
#  Instala TUDO que falta (Git, Python, ffmpeg), baixa o app, configura
#  o ambiente, instala o painel do Premiere e cria um atalho na area de
#  trabalho para o DaVinci.
#
#  Como rodar (PowerShell, como usuario normal):
#     powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
# =====================================================================
$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/lorransouzatreinamentos/viralcut.git"

function Say($msg, $color = "White") { Write-Host $msg -ForegroundColor $color }
function Step($msg) { Say "`n>> $msg" "Cyan" }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Ensure($cmd, $wingetId, $label) {
    if (Have $cmd) { Say "  [ja tem] $label" "DarkGray"; return }
    Say "  [baixando] $label ..." "Yellow"
    winget install --id $wingetId -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
    if (Have $cmd) { Say "  [ok] $label instalado" "Green" }
    else { Say "  [aviso] $label instalado, mas o PATH so atualiza numa nova janela." "Yellow" }
}

Say "=====================================" "Cyan"
Say "   VIRALCUT - instalacao automatica" "Cyan"
Say "=====================================" "Cyan"

# ---------------------------------------------------------------------
Step "1/6  Verificando o winget (gerenciador de pacotes do Windows)"
if (-not (Have winget)) {
    Say "winget nao encontrado." "Red"
    Say "Instale o 'App Installer' pela Microsoft Store e rode este script de novo." "Yellow"
    exit 1
}
Say "  [ok] winget disponivel" "Green"

# ---------------------------------------------------------------------
Step "2/6  Instalando os programas necessarios"
Ensure "git"    "Git.Git"     "Git"
Ensure "ffmpeg" "Gyan.FFmpeg" "ffmpeg (extrai o audio do video)"

# Python: o Windows tem um atalho falso de 'python' que abre a Microsoft Store.
# Por isso testamos se o comando REALMENTE roda, e preferimos o launcher 'py'.
function Test-Python($cmd) {
    try { & $cmd --version 2>&1 | Out-Null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}
function Find-Python {
    if ((Have py)     -and (Test-Python "py"))     { return "py" }
    if ((Have python) -and (Test-Python "python")) { return "python" }
    return $null
}
$PY = Find-Python
if (-not $PY) {
    Say "  [baixando] Python 3.11 ..." "Yellow"
    winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
    $PY = Find-Python
}
if (-not $PY) {
    Say "`nPython foi instalado, mas esta janela ainda nao enxerga." "Yellow"
    Say "FECHE o PowerShell, abra de novo e rode este script novamente." "Yellow"
    exit 1
}
Say "  [ok] Python encontrado ($PY)" "Green"

# ---------------------------------------------------------------------
Step "3/6  Baixando o VIRALCUT"
# Se este script ja esta dentro do repo (usuario clonou antes), usa essa pasta.
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "requirements.txt"))) {
    $Dest = $PSScriptRoot
    Say "  usando o repositorio ja baixado em $Dest" "DarkGray"
    git -C $Dest pull --ff-only 2>$null
} else {
    $Dest = Join-Path $env:USERPROFILE "viralcut"
    if (Test-Path (Join-Path $Dest ".git")) {
        Say "  ja existe em $Dest - atualizando..." "DarkGray"
        git -C $Dest pull --ff-only
    } else {
        Say "  clonando para $Dest" "DarkGray"
        Say "  (se pedir login do GitHub, autorize no navegador)" "DarkGray"
        git clone $RepoUrl $Dest
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $Dest ".git"))) {
            Say "`nFalha ao baixar o repositorio." "Red"
            Say "Se ele e privado, peca ao Lorran para te adicionar como colaborador" "Yellow"
            Say "no GitHub, e faca login quando o Git pedir." "Yellow"
            exit 1
        }
    }
}
Set-Location $Dest

# ---------------------------------------------------------------------
Step "4/6  Preparando o ambiente Python"
if (-not (Test-Path ".venv")) { & $PY -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv\Scripts\pip.exe install --quiet -r requirements.txt
Say "  [ok] dependencias instaladas" "Green"

# ---------------------------------------------------------------------
Step "5/6  Configurando a chave da OpenAI"
$VcDir   = Join-Path $env:USERPROFILE ".viralcut"
$EnvFile = Join-Path $VcDir ".env"
New-Item -ItemType Directory -Force -Path $VcDir | Out-Null
if (Test-Path $EnvFile) {
    Say "  [ja tem] chave configurada em $EnvFile" "DarkGray"
} else {
    $key = Read-Host "  Cole a chave da OpenAI (comeca com sk-)"
    $content = "OPENAI_API_KEY=$key`r`nVIRALCUT_LLM=openai`r`nVIRALCUT_LLM_MODEL=gpt-4o`r`n"
    [System.IO.File]::WriteAllText($EnvFile, $content, (New-Object System.Text.UTF8Encoding($false)))
    Say "  [ok] chave salva" "Green"
}

# ---------------------------------------------------------------------
Step "6/6  Instalando o painel do Adobe Premiere Pro"
# Mesma logica do instalador Mac (scripts/install-premiere.sh), adaptada para
# Windows: bundle ExtendScript concatenado (o @include do CEP nao carrega de
# forma confiavel) + PlayerDebugMode via registro (equivalente ao 'defaults
# write' do macOS) + copia para a pasta de extensoes CEP do usuario.
$PremiereOK = $false
try {
    $Panel   = Join-Path $Dest "premiere-panel"
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $PVersion = "V." + (Get-Date -Format "dd.MM.yy.HH.mm")

    [System.IO.File]::WriteAllText((Join-Path $Panel "client\version.js"), "window.__VIRALCUT_VERSION = `"$PVersion`";`n", $Utf8NoBom)
    [System.IO.File]::WriteAllText((Join-Path $Panel "host\version.jsx"), "var VIRALCUT_BUILD = `"$PVersion`";`n", $Utf8NoBom)

    # Bundle: concatena json2 + version + timeline num UNICO arquivo (VIRALCUT
    # fica definido INLINE, nao via @include -- mesmo motivo do Mac).
    $bundleText = (Get-Content (Join-Path $Panel "host\json2.jsx") -Raw) + "`n" +
                  (Get-Content (Join-Path $Panel "host\version.jsx") -Raw) + "`n" +
                  (Get-Content (Join-Path $Panel "host\timeline.jsx") -Raw)
    [System.IO.File]::WriteAllText((Join-Path $Panel "host\bundle.jsx"), $bundleText, $Utf8NoBom)

    # app.js identico ao da UI compartilhada (fonte unica de logica)
    Copy-Item (Join-Path $Dest "ui\app.js") (Join-Path $Panel "client\app.js") -Force

    # PlayerDebugMode via registro do usuario (nao precisa de admin/HKLM)
    foreach ($csxs in 9, 10, 11, 12) {
        $regKey = "HKCU:\Software\Adobe\CSXS.$csxs"
        New-Item -Path $regKey -Force | Out-Null
        New-ItemProperty -Path $regKey -Name "PlayerDebugMode" -Value "1" -PropertyType String -Force | Out-Null
    }

    # Copia o painel para a pasta de extensoes CEP (por usuario, sem admin)
    $CepDest = Join-Path $env:APPDATA "Adobe\CEP\extensions\VIRALCUT"
    if (Test-Path $CepDest) { Remove-Item $CepDest -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $CepDest | Out-Null
    Copy-Item (Join-Path $Panel "*") $CepDest -Recurse -Force

    Say "  [ok] painel Premiere instalado ($PVersion)" "Green"
    $PremiereOK = $true
} catch {
    Say "  [aviso] nao consegui instalar o painel do Premiere: $($_.Exception.Message)" "Yellow"
}

# ---------------------------------------------------------------------
# Atalho na area de trabalho
try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path $desktop "VIRALCUT.lnk"))
    $lnk.TargetPath       = Join-Path $Dest "viralcut.bat"
    $lnk.WorkingDirectory = $Dest
    $lnk.Save()
    Say "`n  [ok] atalho 'VIRALCUT' criado na area de trabalho" "Green"
} catch { Say "  (nao consegui criar o atalho - use o viralcut.bat na pasta)" "DarkGray" }

# ---------------------------------------------------------------------
Say "`n=====================================" "Green"
Say "   PRONTO!" "Green"
Say "=====================================" "Green"

Say "`n--- DAVINCI RESOLVE ---" "Cyan"
Say "Ajuste unico (uma vez so):" "White"
Say "  Preferences > System > General > 'External scripting using' = Local" "Yellow"
Say "Para usar:" "White"
Say "  1. Abra o DaVinci Resolve (Studio) com um projeto e uma timeline" "White"
Say "  2. Clique no atalho VIRALCUT na area de trabalho" "White"
Say "  3. O app abre no navegador. Selecionar -> Analisar -> Objetivo -> Aplicar" "White"

if ($PremiereOK) {
    Say "`n--- ADOBE PREMIERE PRO ---" "Cyan"
    Say "Ajuste unico (uma vez so, se o Premiere ja estiver aberto):" "White"
    Say "  FECHE o Premiere Pro e abra de novo (o painel novo so aparece apos reiniciar)" "Yellow"
    Say "Para usar:" "White"
    Say "  1. Abra o Premiere com um projeto e uma sequencia" "White"
    Say "  2. Menu Window > Extensions > VIRALCUT" "White"
    Say "  3. Selecionar -> Analisar -> Objetivo -> Aplicar" "White"
}

Say "`nPara atualizar depois: clique na logo do app (canto superior esquerdo)." "DarkGray"
Say "No Premiere, apos atualizar, feche e reabra o painel (Window > Extensions)." "DarkGray"
