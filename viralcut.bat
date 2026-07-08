@echo off
REM VIRALCUT — launcher (Windows / DaVinci Resolve Studio)
REM Puxa a versao nova, sobe o servidor local e abre o app no navegador.
setlocal
cd /d "%~dp0"

echo Buscando atualizacoes...
git pull --ff-only 2>nul

REM DaVinci Resolve scripting (Windows) — necessario para o app falar com o Resolve
set "RESOLVE_SCRIPT_API=%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
set "RESOLVE_SCRIPT_LIB=C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
set "PYTHONPATH=%PYTHONPATH%;%RESOLVE_SCRIPT_API%\Modules"

call .venv\Scripts\activate.bat
start "" http://127.0.0.1:8756/ui/
echo VIRALCUT rodando. Deixe esta janela aberta. Feche para encerrar o app.
python -m uvicorn core.main:app --port 8756 --reload
