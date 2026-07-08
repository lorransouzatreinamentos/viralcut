@echo off
REM =====================================================================
REM  VIRALCUT - launcher (Windows / DaVinci Resolve Studio)
REM  Puxa a versao nova, sobe o servidor local e abre o app no navegador.
REM =====================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo.
  echo O ambiente nao esta instalado.
  echo Rode primeiro:  powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
  echo.
  pause
  exit /b 1
)

echo Buscando atualizacoes...
git pull --ff-only 2>nul

REM DaVinci Resolve scripting (Windows) - necessario para o app falar com o Resolve
set "RESOLVE_SCRIPT_API=%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
set "RESOLVE_SCRIPT_LIB=C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
set "PYTHONPATH=%PYTHONPATH%;%RESOLVE_SCRIPT_API%\Modules"

call .venv\Scripts\activate.bat

REM Abre o navegador so depois que o servidor subir (4s)
start "" /b powershell -NoProfile -Command "Start-Sleep -Seconds 4; Start-Process 'http://127.0.0.1:8756/ui/'"

echo.
echo VIRALCUT rodando. Deixe esta janela aberta.
echo Feche esta janela para encerrar o app.
echo.
python -m uvicorn core.main:app --port 8756 --reload
