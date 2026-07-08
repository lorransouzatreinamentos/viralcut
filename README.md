# VIRALCUT

Extensão de cortes virais assistidos por IA para Adobe Premiere Pro e DaVinci Resolve Studio.
Ver [`PLANO_MESTRE.md`](PLANO_MESTRE.md) para a especificação completa.

## Setup do Core (dev)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencher chaves de API
```

## Rodar

```bash
source .venv/bin/activate
uvicorn core.main:app --port 8756 --reload
```

Verificar: `curl http://127.0.0.1:8756/health`

## Testes

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## Requisitos por plataforma

- **Python 3.11+** (o modelo usa sintaxe `X | None`, incompatível com o Python 3.9 do macOS).
- **DaVinci Resolve Studio** (versão paga) — scripting externo não funciona na versão gratuita. Ver `.env.example` para as variáveis `RESOLVE_SCRIPT_API`/`RESOLVE_SCRIPT_LIB`.
- **Adobe Premiere Pro** — painel CEP (Bolt CEP), instalação separada em `premiere-panel/` (Fase 4).

## Status

**Fases 0–4 do Core construídas e testadas** (47 testes passando). Ver checklist completo em `PLANO_MESTRE.md` seção 20.

| Fase | O que está pronto | Verificado |
|------|-------------------|-----------|
| 0 · Fundação | Core FastAPI, modelo de dados com invariantes de word-ID | ✅ testes + servidor |
| 1 · Transcrição DaVinci | adapter (leitura timeline + export áudio) + pipeline Whisper (compressão/chunking) | ✅ testes (ffmpeg real) + endpoints |
| 2 · Cortes virais IA | `viral.py` — IA responde só com IDs; timecode 100% derivado das palavras | ✅ testes (inclui prova estrutural anti-FastVideo) |
| 3 · Aplicar DaVinci | `adapters/davinci.py` — CreateTimelineFromClips + SetClipColor + verificação | ✅ testes puros (frame-math) + mock da API Resolve |
| 4 · Premiere | `premiere_plan.py` (tick-math testada) + `timeline.jsx` (ExtendScript, API verificada) + painel CEP + UI | ✅ plan/UI testados · ⏳ ExtendScript e install exigem Premiere para verificar |

### Testado de verdade nesta máquina
- 47 testes unitários (modelo, transcrição, IA, mapeamento de frames DaVinci, tick-math Premiere).
- Compressão/split de áudio com **ffmpeg real** (áudio sintético).
- Servidor sobe, todas as 8 rotas do contrato respondem e degradam com erro claro em PT-BR quando falta DaVinci/API key.
- UI compartilhada renderiza e é servida em `/ui`.

### Instalar e testar no Premiere (Premiere Pro 2026 detectado nesta máquina)

```bash
bash scripts/install-premiere.sh          # instala o painel CEP + ativa debug mode
cp .env.example .env                       # e preencher OPENAI_API_KEY + ANTHROPIC_API_KEY
.venv/bin/uvicorn core.main:app --port 8756  # deixar o Core rodando
# reiniciar o Premiere -> Window > Extensions > VIRALCUT
```

Fluxo no painel: **Selecionar sequência** (lê a mídia-fonte via `getMediaPath`) → **Analisar cortes virais** (transcreve o arquivo + IA) → marcar cortes → **Aplicar** (cria nova sequência com os cortes coloridos; original intacta).

### Falta verificar EM MÁQUINA COM O EDITOR
- `timeline.jsx` rodando dentro do Premiere 2026 (createSubClip/createNewSequenceFromClips/setColorLabel) — **painel já instalado**, falta abrir e rodar.
- Transcrição ponta a ponta com uma `OPENAI_API_KEY` real (o pipeline ffmpeg+parse está testado; falta a chamada real ao Whisper).
- `adapters/davinci.py` contra um Resolve Studio aberto (a lógica está testada por mocks + funções puras).

Nota: a captura de áudio do Premiere usa `getMediaPath` + ffmpeg (como o FastVideo) — **não precisa de preset `.epr`**.
