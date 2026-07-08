#!/usr/bin/env bash
# Instala/atualiza o painel VIRALCUT como extensao CEP do Premiere Pro (macOS).
# Reexecutavel: sincroniza app.js, re-carimba a versao e recopia o painel.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/Application Support/Adobe/CEP/extensions/VIRALCUT"

# Versao no formato pedido: V.D.M.AA.hh.mm (dia mes ano-2 hora minuto)
VERSION="$(date +'V.%d.%m.%y.%H.%M')"

# Carimba a versao nos dois lados: JS (rodape) e ExtendScript (auto-teste de load).
printf 'window.__VIRALCUT_VERSION = "%s";\n' "$VERSION" > "$ROOT/premiere-panel/client/version.js"
printf 'var VIRALCUT_BUILD = "%s";\n' "$VERSION" > "$ROOT/premiere-panel/host/version.jsx"

# Bundle ExtendScript: concatena json2 + version + timeline num UNICO arquivo
# carregado pelo ScriptPath. VIRALCUT fica definido INLINE (nao via @include, que
# o CEP nao processa de forma confiavel — motivo do "EvalScript error" anterior).
H="$ROOT/premiere-panel/host"
cat "$H/json2.jsx" "$H/version.jsx" "$H/timeline.jsx" > "$H/bundle.jsx"

# Mantem o app.js do painel identico ao da UI compartilhada (fonte unica de logica).
cp "$ROOT/ui/app.js" "$ROOT/premiere-panel/client/app.js"

# PlayerDebugMode: permite extensao nao-assinada carregar (CSXS 10-13).
for v in 10 11 12 13; do
  defaults write "com.adobe.CSXS.$v" PlayerDebugMode 1 2>/dev/null || true
done

rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$ROOT/premiere-panel/." "$DEST/"

echo "VIRALCUT $VERSION instalado em: $DEST"
