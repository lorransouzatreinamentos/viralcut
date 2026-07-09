#!/usr/bin/env bash
# =============================================================================
#  VIRALCUT — instalador macOS (DaVinci Resolve Studio e/ou Premiere Pro)
#
#  Cole no Terminal:
#    curl -fsSL https://raw.githubusercontent.com/lorransouzatreinamentos/viralcut/main/install-mac.sh | bash
#
#  REGRA: NAO substitui nada que ja existe. Git, Python e ffmpeg ja instalados
#  (usados por outras IAs na maquina) sao reaproveitados como estao. So instala
#  o que falta. Reexecutavel a vontade.
# =============================================================================
set -euo pipefail

REPO="https://github.com/lorransouzatreinamentos/viralcut.git"
DEST="$HOME/viralcut"
VC_DIR="$HOME/.viralcut"

# Rodando via `curl | bash`, o stdin e o script -- perguntas precisam do terminal.
# `[ -r /dev/tty ]` nao basta: o arquivo existe mesmo sem terminal anexado e o
# read falha com "Device not configured". Abrir o fd 3 e o teste que vale.
if exec 3</dev/tty 2>/dev/null; then HAS_TTY=1; else HAS_TTY=0; fi

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
skip() { printf '  \033[90m•\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '\033[31mERRO:\033[0m %s\n' "$1" >&2; exit 1; }

bold "VIRALCUT — instalacao (macOS)"
echo

# --- 1. Homebrew (so se faltar) ----------------------------------------------
bold "1/7  Homebrew"
if [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"; skip "ja instalado (Apple Silicon)"
elif [ -x /usr/local/bin/brew ]; then
  eval "$(/usr/local/bin/brew shellenv)"; skip "ja instalado (Intel)"
else
  [ "$HAS_TTY" = 1 ] || die "Homebrew precisa de um Terminal interativo. Abra o Terminal e rode o comando de novo."
  echo "  Instalando (pede sua senha do Mac)…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" <&3
  [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)"
  command -v brew >/dev/null || die "Homebrew nao ficou disponivel. Abra um Terminal novo e rode de novo."
  ok "instalado"
fi

# ensure <comando> <formula>  — instala SO se o comando nao existir
ensure() {
  if command -v "$1" >/dev/null 2>&1; then
    skip "$1 ja existe ($(command -v "$1")) — mantido como esta"
  else
    echo "  Instalando $2…"; brew install "$2" >/dev/null; ok "$1 instalado"
  fi
}

# --- 2. git + ffmpeg ----------------------------------------------------------
bold "2/7  Ferramentas base"
ensure git git
ensure ffmpeg ffmpeg

# --- 3. Python 3.10+ ----------------------------------------------------------
# Resolve carrega o fusionscript.so via 'imp' em builds antigos -> 3.12 quebra.
# Preferimos 3.11. Se ja houver 3.10/3.11 na maquina, usamos e nao instalamos nada.
bold "3/7  Python"
PY=""
for c in python3.11 python3.10 python3; do
  p="$(command -v "$c" 2>/dev/null || true)"
  [ -n "$p" ] || continue
  if "$p" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,12) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done
if [ -n "$PY" ]; then
  skip "usando $PY ($("$PY" -V 2>&1)) — nada substituido"
else
  echo "  Nenhum Python 3.10/3.11 encontrado. Instalando python@3.11 ao lado dos existentes…"
  brew install python@3.11 >/dev/null
  PY="$(brew --prefix)/opt/python@3.11/bin/python3.11"
  [ -x "$PY" ] || die "python@3.11 nao encontrado apos instalar"
  ok "python@3.11 instalado (os outros Pythons ficaram intactos)"
fi

# --- 4. Codigo ----------------------------------------------------------------
bold "4/7  Codigo do VIRALCUT"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --ff-only >/dev/null 2>&1 || warn "nao consegui atualizar (mudancas locais?) — seguindo com a versao atual"
  ok "atualizado em $DEST"
else
  # clone completo (nao --depth 1): o auto-update do launcher usa `git pull`
  git clone "$REPO" "$DEST" >/dev/null 2>&1 || die "falha ao clonar $REPO"
  ok "clonado em $DEST"
fi

# --- 5. Ambiente Python + faster-whisper --------------------------------------
bold "5/7  Ambiente Python (venv + transcricao local)"
[ -x "$DEST/.venv/bin/python" ] || "$PY" -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --quiet --upgrade pip >/dev/null
"$DEST/.venv/bin/pip" install --quiet -r "$DEST/requirements.txt" >/dev/null
ok "dependencias instaladas"

# faster-whisper e OBRIGATORIO: a transcricao roda sempre local, nunca na nuvem.
echo "  Instalando faster-whisper (transcricao local, ~200MB)…"
"$DEST/.venv/bin/pip" install --quiet faster-whisper >/dev/null \
  || die "falha ao instalar faster-whisper. Sem ele o app nao transcreve (nao usamos nuvem)."
ok "faster-whisper instalado"

# --- 6. Chave da OpenAI (so pros CORTES; o audio nunca sai da maquina) --------
bold "6/7  Chave da OpenAI"
mkdir -p "$VC_DIR"
if [ -f "$VC_DIR/.env" ] && grep -q '^OPENAI_API_KEY=sk-' "$VC_DIR/.env" 2>/dev/null; then
  skip "chave ja configurada em $VC_DIR/.env"
else
  echo "  A chave e usada so para a IA escolher os cortes (texto)."
  echo "  O video e o audio nunca saem do seu computador."
  KEY=""
  if [ "$HAS_TTY" = 1 ]; then
    printf "  Cole a chave (sk-…) e tecle Enter: "
    read -r KEY <&3 || KEY=""
  fi
  if [ -n "$KEY" ]; then
    printf 'OPENAI_API_KEY=%s\nVIRALCUT_TRANSCRIBE=local\n' "$KEY" > "$VC_DIR/.env"
    chmod 600 "$VC_DIR/.env"
    ok "salva em $VC_DIR/.env (permissao 600)"
  else
    printf 'VIRALCUT_TRANSCRIBE=local\n' > "$VC_DIR/.env"; chmod 600 "$VC_DIR/.env"
    warn "sem chave: transcricao funciona, mas os cortes por IA nao. Edite $VC_DIR/.env depois."
  fi
fi

# --- 7. DaVinci Resolve + Premiere -------------------------------------------
bold "7/7  Integracao com os editores"

# Caminhos oficiais do scripting do Resolve no macOS. A .app pode estar solta em
# /Applications ou dentro da pasta "DaVinci Resolve" — procuramos nos dois.
RS_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
RS_LIB=""
for c in \
  "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so" \
  "/Applications/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"; do
  [ -f "$c" ] && { RS_LIB="$c"; break; }
done

if [ -n "$RS_LIB" ]; then
  ok "DaVinci Resolve encontrado"
else
  warn "DaVinci Resolve nao encontrado — o launcher assume o caminho padrao."
  RS_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
fi

cat > "$DEST/viralcut.command" <<LAUNCHER
#!/usr/bin/env bash
# VIRALCUT — launcher (macOS). Atualiza, sobe o servidor local e abre o app.
cd "\$(dirname "\$0")"
git pull --ff-only >/dev/null 2>&1 || true

export RESOLVE_SCRIPT_API="$RS_API"
export RESOLVE_SCRIPT_LIB="$RS_LIB"
export PYTHONPATH="\$PYTHONPATH:$RS_API/Modules/"

( sleep 4; open "http://127.0.0.1:8756/ui/" ) &
exec .venv/bin/python -m uvicorn core.main:app --port 8756
LAUNCHER
chmod +x "$DEST/viralcut.command"
ok "launcher criado: $DEST/viralcut.command"

# Instala o painel SEMPRE (igual ao Windows). Copiar pra pasta de extensoes CEP
# e inofensivo mesmo sem o Premiere -- assim, se o usuario instalar o Premiere
# depois, o painel ja esta la (nao precisa rerodar o instalador).
if bash "$DEST/scripts/install-premiere.sh" >/dev/null 2>&1; then
  if ls -d /Applications/Adobe\ Premiere\ Pro* >/dev/null 2>&1; then
    ok "painel do Premiere instalado"
  else
    ok "painel do Premiere preparado (Premiere ainda nao detectado — aparece quando instalar)"
  fi
else
  warn "nao consegui instalar o painel do Premiere (segue funcionando pro DaVinci)"
fi

# --- Final --------------------------------------------------------------------
echo
bold "Pronto."
echo
echo "  DaVinci Resolve (Studio):"
echo "    1. Resolve > Preferences > System > General:"
echo "       'External scripting using' = Local"
echo "    2. Abra seu projeto e a TIMELINE na pagina Edit"
echo "    3. Rode:  open \"$DEST/viralcut.command\""
echo
echo "  Premiere Pro:"
echo "    Janela > Extensões > VIRALCUT"
echo
echo "  A primeira transcricao baixa o modelo de voz (~466MB, uma vez so)."
echo "  Transcricoes ficam em cache: reabrir o mesmo video e instantaneo."
echo
