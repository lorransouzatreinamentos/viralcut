# PLANO MESTRE — VIRALCUT

> Extensão de cortes virais assistidos por IA para **Adobe Premiere Pro** e **DaVinci Resolve**.
> Documento de especificação executável. Versão 1.0 — 2026-07-07.
> Codinome de trabalho: **VIRALCUT** (nome final é decisão do dono do produto).

---

## 0. COMO USAR ESTE DOCUMENTO (leia primeiro — instruções para o modelo executor)

Você é o modelo que vai **construir** este produto. Este documento foi escrito para que você execute sem precisar tomar decisões arquiteturais grandes — elas já foram tomadas e justificadas aqui, com base em pesquisa técnica das APIs reais de ambos os editores (julho/2026).

Regras de execução:

1. **Não improvise a arquitetura.** As decisões da Seção 4 são inegociáveis e existem para evitar repetir o fracasso do FastVideo (Seção 1). Se você achar que precisa mudar algo estrutural, pare e explique ao dono do produto antes.
2. **Construa na ordem das fases (Seção 15).** Cada fase tem critério de aceitação. Não avance sem o critério anterior verde. O MVP é a Fase 1+2+3. Frankenbite e Limpar Espaços são fases posteriores — não as construa antes do corte básico funcionar de ponta a ponta.
3. **A fonte de verdade é sempre o timecode a nível de palavra (word ID).** Nunca deixe a IA inventar timecodes. Seção 7 e 12 explicam.
4. **Trate os dois editores como capacidades assimétricas, não simétricas.** DaVinci remonta timelines de forma limpa; Premiere é frágil. O design (Seção 4) neutraliza isso usando **reconstrução não-destrutiva** nos dois. Não tente forçar "razor in-place" — foi o que matou o FastVideo.
5. **Cada operação de host precisa ser verificável.** Depois de aplicar cortes, releia a timeline e confirme que o resultado bate com o esperado. Operações "cegas" são proibidas.
6. **Confirme a versão-alvo** antes de codar o adapter: Premiere Pro 2025 (CEP) e DaVinci Resolve **Studio** 19/20. DaVinci free NÃO permite scripting externo (Seção 3).

---

## 1. CONTEXTO E LIÇÕES DO FASTVIDEO (por que a v1 falhou)

O FastVideo foi uma tentativa anterior (Premiere-only, CEP). O código real (`FASTVIDEO-v2.0/host/timeline.jsx`) revela exatamente por que "era confuso, tinha falhas e não funcionava":

| Erro no FastVideo | Consequência | Correção neste plano |
|---|---|---|
| Usava `qe.project.getActiveSequence().razor(String(tc))` (QE DOM) para cortar in-place | QE DOM é **não-documentado e não-suportado** pela Adobe; quebra entre versões do Premiere sem aviso | Não editamos in-place. Reconstrução não-destrutiva em nova sequência (Seção 4) |
| Chamava `tClip.setColorLabel(cut.color)` em **trackItem** (clip na timeline) | Esse método **não existe** em trackItem — só em projectItem. A cor **nunca aplicava**. | Cor via `projectItem.setColorLabel()` no subclip (Premiere) e `TimelineItem.SetClipColor()` (DaVinci) — ambos existem e funcionam |
| Misturava 3 estratégias de corte no mesmo código (`razor` + `createSubClip` + `overwriteClip` com fallbacks encadeados) | Comportamento imprevisível, estados parciais, "às vezes funciona" | **Uma** estratégia por host, determinística e testada |
| Edição destrutiva da timeline original | Sem undo confiável; se falhava no meio, deixava a timeline quebrada | Original nunca é tocada. Saída vai para timeline/sequência nova |
| Premiere-only | Não atendia DaVinci | Core agnóstico + 2 adapters (Seção 4) |
| Lógica de IA/transcrição dentro do painel CEP (ExtendScript ES3) | ES3 é lento, sem JSON nativo, sem async real; payloads grandes travavam | Toda a inteligência num **Core Engine Python** fora do editor (Seção 4) |

**Princípio que resume a lição:** o editor de vídeo é um *ator burro* que recebe uma lista de cortes já calculada e a materializa. Toda a inteligência (transcrição, IA, modelo de dados) vive fora dele.

### 1.1 CAUSA RAIZ CONFIRMADA — por que os "cortes virais" gerados eram ruins (PRIMORDIAL)

Este foi identificado como o **maior problema** do FastVideo. Evidência no código real (`client/js/providers.js` + `audio-transcriber.js`):

- O FastVideo **já coletava word-level timestamps** do Whisper (`timestamp_granularities[]: ["segment","word"]` — `audio-transcriber.js:275-276`). O dado preciso existia.
- Mas o prompt (`buildUserPrompt`, `providers.js:352`) só passava **segmentos** (frases inteiras do Whisper, tipicamente 3-10s) para a IA — o array `words[]` nunca chegava ao LLM nem à validação de corte.
- Pior: a IA era instruída a **copiar manualmente** o número (`"start": <SEGUNDOS_EXATOS_DO_SEGMENTO>`) de uma lista que ela lia no prompt. LLMs erram sistematicamente nesse tipo de cópia numérica literal (trocam dígito, arredondam, pegam o segmento vizinho).
- Por cima disso, `snapToSegmentBoundary(time, segments, tolerance=2.5)` (`providers.js:784`) "corrigia" o valor errado saltando para o limite de **segmento** mais próximo dentro de 2.5 segundos — silenciosamente, sem verificação semântica. Um corte podia terminar até 2.5s deslocado da intenção real, cortando no meio de uma frase.
- Consequência combinada: mesmo tendo dado preciso disponível no pipeline, o corte final nunca era mais preciso que "a frase inteira mais próxima, com até 2.5s de erro adicional" — exatamente o padrão de corte "estranho"/"no meio da fala" relatado pelo usuário.

**Correção que este plano já aplica, agora endurecida por essa evidência (ver Seção 7 e 12):**
1. A IA **nunca** emite um número de timestamp. Ela só referencia **IDs** (de palavra ou segmento) que já existem no dado.
2. Timecode final é **sempre calculado em código** a partir do `word_id`, nunca aceito literalmente da resposta do LLM. Se a resposta do LLM contiver um campo `start`/`end` numérico, ele é **ignorado e descartado** — não é usado nem para fallback.
3. Granularidade de corte é **word-level**, não segment-level. Fronteiras de segmento/frase servem só para o contexto que a IA lê, nunca para o corte em si.
4. Zero tolerância de "snap" solto — o snap é sempre exato para `word.start`/`word.end` + padding fixo configurável (Seção 7), nunca uma busca por "boundary mais próximo dentro de N segundos".

---

## 2. PRINCÍPIOS DE DESIGN INEGOCIÁVEIS

1. **Separação estrita**: inteligência (Core) ≠ materialização na timeline (Adapter). O Core não sabe nada de Premiere/DaVinci; o Adapter não sabe nada de IA.
2. **Não-destrutivo por padrão**: a timeline original do usuário nunca é modificada. Resultados vão para uma timeline/sequência nova, claramente nomeada.
3. **Timecode a nível de palavra é a fonte de verdade única.** Todo corte referencia `word_id`s; timecodes derivam deles. A IA nunca escreve um timestamp — ela referencia IDs.
4. **Verificabilidade**: toda operação de host lê o resultado de volta e valida. Nada é "fire and forget".
5. **Idempotência e atomicidade prática**: reexecutar não duplica; falha no meio não corrompe (porque trabalhamos em cópia/nova timeline).
6. **Rápido e simples de usar**: 3 cliques para o resultado principal — Selecionar timeline → Analisar → Aplicar. Sem telas confusas.
7. **Degradação graciosa**: sem GPU? usa API. Sem API key? avisa claramente. Editor numa versão sem capacidade X? desabilita o botão e explica, não falha silenciosamente.

---

## 3. REALIDADE DAS PLATAFORMAS (a assimetria que define tudo)

Pesquisa das APIs reais (fontes ao final da seção). **Leia isto antes de qualquer código de host.**

### 3.1 Tabela de capacidades

| Operação | Premiere Pro (CEP/ExtendScript) | DaVinci Resolve (Studio, Python API) |
|---|---|---|
| Ler timeline/sequência ativa | ✅ `app.project.activeSequence` | ✅ `project.GetCurrentTimeline()` |
| Listar clips de uma track | ✅ `sequence.videoTracks[i].clips` | ✅ `timeline.GetItemListInTrack("video", i)` |
| **Cortar/dividir clip num timecode** | ⚠️ **Só via QE DOM** (`qe...razor()`) — não suportado, frágil | ❌ **Não existe** nenhum razor/split na API |
| **Adicionar subclip por in/out à timeline** | ✅ `videoTrack.insertClip(subclip, offsetSec)` / `overwriteClip(item, offset, inTicks, outTicks)` | ✅ `MediaPool.AppendToTimeline([clipInfo])` com `startFrame`/`endFrame`/`recordFrame` |
| **Criar subclip a partir de item** | ✅ `projectItem.createSubClip(name, inTicks, outTicks, ...)` | ✅ via `clipInfo` no append (não precisa criar subclip separado) |
| **Criar nova timeline a partir de trechos** | ✅ criar sequência + inserir subclips | ✅ `MediaPool.CreateTimelineFromClips(name, [clipInfo])` (rota nativa e limpa) |
| **Colorir clip na timeline** | ❌ trackItem **não** tem cor. ✅ mas `projectItem.setColorLabel(idx)` funciona (0–16) e o subclip herda | ✅ `TimelineItem.SetClipColor("Blue")` (cores nomeadas) |
| Marcadores coloridos | ✅ `sequence.markers.createMarker(sec)` + `.setColorByIndex()` | ✅ `timeline.AddMarker(frame, color, name, note, dur, customData)` |
| **Exportar áudio da timeline** | ✅ `sequence.exportAsMediaDirect(path, eprPreset, workArea)` ou fila no AME | ✅ Deliver: `SetRenderSettings` + `AddRenderJob` + `StartRendering` (formato WAV) |
| Transcrição nativa | ❌ (não há; usa serviço externo) | ✅ `timeline.TranscribeAudio()` → lê subtitle track (mas word-level externo é mais preciso) |
| Painel HTML embutido | ✅ CEP panel (o produto roda dentro do Premiere) | ⚠️ Workflow Integration Plugin (Electron, **Studio-only**) — ou script Python externo com UI própria |
| Scripting externo (processo próprio) | via CEP (dentro do app) | ✅ **só na versão Studio** (paga). Free = só console interno |
| Precisão de tempo | ticks: **254016000000 ticks/segundo** (string em ES3) | **frames inteiros** (respeitar fps do projeto, cuidado com 29.97 DF) |

### 3.2 Conclusões arquiteturais forçadas por esta tabela

- **Nenhum dos dois corta clip in-place de forma confiável.** Logo, a estratégia unificada é **remontar** os trechos aprovados numa **nova timeline** (DaVinci: `CreateTimelineFromClips`; Premiere: nova sequência + subclips). Isso é limpo nos dois e resolve de quebra o problema de cor do Premiere (subclip novo herda a cor do projectItem).
- **DaVinci exige Studio.** Documente isso como requisito. Free não roda o app externo.
- **A UI vive no Premiere via CEP** (dentro do app) e **no DaVinci via processo externo** (o Core Python dirige o Resolve direto). A mesma UI web é reaproveitada nos dois (Seção 4).
- **Word-level timestamps não vêm de graça de nenhum editor** → transcrição é responsabilidade do Core (WhisperX / whisper.cpp / API), não do editor.

### 3.3 Fontes
- Premiere: ppro-scripting.docsforadobe.dev (Sequence, Marker, ProjectItem); Adobe UXP dev docs; fóruns Adobe (razor via QE, ausência de setColorLabel em trackItem — DVAPR-4217788); Hyper Brew Bolt CEP.
- DaVinci: README de scripting oficial (Developer/Scripting); dump da API v21 (X-Raym gist); fórum Blackmagic (free vs Studio, transcrição→subtitles); Workflow Integration README.
- IA/transcrição: OpenAI speech-to-text docs (whisper-1 + `timestamp_granularities`); WhisperX (arXiv 2303.00747); videogrep (supercut); OpenTimelineIO.

---

## 4. DECISÃO ARQUITETURAL CENTRAL

### 4.1 Visão

```
┌─────────────────────────────────────────────────────────────┐
│                     CORE ENGINE (Python)                     │
│              FastAPI local — http://127.0.0.1:8756           │
│                                                              │
│  • Transcrição (WhisperX / whisper.cpp / OpenAI Whisper)     │
│  • IA de cortes virais (LLM)                                 │
│  • IA de frankenbite (LLM)                                   │
│  • Detecção de gaps (algoritmo puro)                         │
│  • Modelo de dados canônico (JSON, word IDs)                 │
│  • Serve a UI web (HTML/React) em /ui                        │
│  • DaVinci Adapter EMBUTIDO (Python → Resolve API direta)    │
└───────────────┬──────────────────────────────┬──────────────┘
                │ HTTP (localhost)              │ import direto (mesmo processo)
                │                               │
     ┌──────────▼───────────┐         ┌─────────▼──────────────┐
     │  PREMIERE FRONT-END   │         │   DAVINCI FRONT-END     │
     │  (CEP panel, dentro   │         │  (UI web no browser/    │
     │   do Premiere)        │         │   webview; Core dirige  │
     │                       │         │   o Resolve via API)    │
     │  UI web + bridge      │         │                         │
     │  ExtendScript         │         │                         │
     └──────────┬───────────┘         └─────────┬──────────────┘
                │ evalScript                      │ Resolve scripting API
     ┌──────────▼───────────┐         ┌─────────▼──────────────┐
     │  Premiere ExtendScript │         │  DaVinci Resolve Studio │
     │  (materializa cortes)  │         │  (materializa cortes)   │
     └───────────────────────┘         └─────────────────────────┘
```

### 4.2 Por que Core em Python

1. **WhisperX e o ecossistema de transcrição são Python.**
2. **A API de scripting do DaVinci é Python** → no DaVinci o Core dirige a timeline **diretamente**, sem ponte. Menos superfície de falha.
3. LLM calls e modelo de dados são triviais em Python.
4. FastAPI serve tanto a API quanto a UI estática — um processo só.

### 4.3 Por que UI web compartilhada

A mesma SPA (React ou HTML+JS vanilla) roda:
- **Dentro do Premiere** como painel CEP (Bolt CEP), falando com o Core via `fetch("http://127.0.0.1:8756/...")` e aplicando cortes via `CSInterface.evalScript`.
- **No DaVinci** aberta num webview/browser servida pelo próprio Core; a aplicação de cortes é feita pelo Core em Python (o front só chama `POST /apply`).

Uma flag `host: "premiere" | "davinci"` (detectada na inicialização) decide o caminho de "aplicar". Todo o resto da UI é idêntico.

### 4.4 Fluxo de "aplicar cortes" por host

- **DaVinci**: `POST /apply` → Core chama Resolve API diretamente (`CreateTimelineFromClips` + `SetClipColor`). Simples e robusto.
- **Premiere**: `POST /apply` retorna um **plano de cortes** (JSON com offsets em segundos/ticks); o painel CEP recebe e executa via `evalScript` chamando funções ExtendScript. O Core não toca no Premiere; ele só calcula o plano.

> Regra: **o Core calcula, o Adapter materializa.** No DaVinci o "Adapter" é código Python do próprio Core; no Premiere é ExtendScript acionado pelo painel.

---

## 5. ARQUITETURA DE SISTEMA — componentes

| Componente | Tecnologia | Responsabilidade |
|---|---|---|
| **core/** | Python 3.11 + FastAPI + uvicorn | Servidor local; orquestra tudo |
| core/transcribe.py | WhisperX (padrão) / whisper.cpp (Mac) / OpenAI API (fallback) | áudio → transcrição word-level |
| core/viral.py | LLM (Claude/GPT) | transcrição → cortes virais (JSON validado) |
| core/frankenbite.py | LLM | transcrição → montagem de narrativa nova |
| core/gaps.py | algoritmo puro (sem IA) | detectar gaps numa lista de clips |
| core/model.py | pydantic | schema canônico + validação |
| core/adapters/davinci.py | Resolve Python API | materializa no DaVinci |
| core/adapters/premiere_plan.py | Python | calcula plano de cortes para o Premiere |
| **ui/** | React + Vite (ou HTML/JS vanilla) | interface única |
| **premiere-panel/** | Bolt CEP (CEP + ExtendScript) | empacota a UI como painel Premiere + bridge ExtendScript |
| premiere-panel/host/timeline.jsx | ExtendScript | funções de materialização no Premiere |
| **davinci-launcher/** | Python + webview (pywebview) ou instruções de menu Workflow Integration | abre a UI e conecta ao Core dentro do contexto do Resolve |

---

## 6. STACK TECNOLÓGICO DEFINITIVO

- **Linguagem do Core**: Python 3.11+.
- **Servidor**: FastAPI + uvicorn, bind em `127.0.0.1:8756` (porta fixa; se ocupada, +1 até achar livre e reportar).
- **Transcrição** (estratégia em camadas, escolhida por disponibilidade):
  1. **MVP / fallback universal**: OpenAI Whisper API (`whisper-1`, `response_format="verbose_json"`, `timestamp_granularities=["word"]`). ~$0.006/min. Limite 25 MB → extrair áudio como MP3 16 kHz mono (20 min cabe em 1 chamada; >25 min = chunking com offset acumulado).
  2. **Local no Mac (Apple Silicon)**: whisper.cpp com Metal + `--dtw` (token-level timestamps) OU faster-whisper (CPU). Custo zero, privacidade.
  3. **Local com GPU NVIDIA**: WhisperX (melhor word-alignment, VAD embutido). ~12× real-time.
  - **Sempre com VAD** (Silero) para não alucinar em silêncios e dar fronteiras limpas.
- **LLM de cortes**: Claude (Sonnet/Opus) ou GPT, `temperature=0.1`, saída JSON validada por schema. Configurável.
- **Premiere**: Bolt CEP (Vite + TS + React) — boilerplate da Hyper Brew, padrão de mercado. Inclui `json2.js` no ExtendScript (ES3 não tem JSON).
- **DaVinci**: Resolve **Studio** 19/20; Python API via env vars `RESOLVE_SCRIPT_API`, `RESOLVE_SCRIPT_LIB`, `PYTHONPATH`.
- **Camada de export opcional** (fase futura): OpenTimelineIO para gerar FCPXML/EDL quando quiser exportar sem estar dentro do editor.

---

## 7. MODELO DE DADOS CANÔNICO (a espinha dorsal)

Formato único, agnóstico de editor. **Word IDs são a chave; timecodes derivam deles.**

```jsonc
// project_state.json — a fonte de verdade de uma análise
{
  "schema_version": "1.0",
  "media": {
    "source": "premiere" | "davinci",
    "clip_ref": "…",          // referência ao item de origem no editor (ver adapters)
    "fps": 30.0,
    "duration_sec": 1200.0,
    "audio_export_path": "/tmp/viralcut/seq_abc_audio.mp3"
  },
  "words": [
    { "id": 0, "text": "todo",  "start": 12.10, "end": 12.34 },
    { "id": 1, "text": "mundo", "start": 12.34, "end": 12.60 }
    // … todas as palavras, em ordem
  ],
  "segments": [
    { "id": 0, "start": 12.10, "end": 18.90, "text": "todo mundo faz isso errado", "word_ids": [0,1,2,3,4] }
  ],
  "clips": [
    {
      "id": "vir_001",
      "type": "highlight",           // corte viral "direto"
      "titulo": "O erro que 90% comete",
      "start_word_id": 820,
      "end_word_id": 910,
      "start": 412.30,               // DERIVADO de words[820].start (nunca escrito pela IA)
      "end": 458.90,                 // DERIVADO de words[910].end
      "score": 87,
      "motivo": "gancho contraintuitivo + tensão resolvida",
      "hook_first_3s": "Todo mundo faz isso errado...",
      "approved": false,
      "color": "Blue"                // cor de destaque na timeline
    },
    {
      "id": "frk_001",
      "type": "frankenbite",         // montagem de vários trechos
      "titulo": "Os 3 pilares",
      "approved": false,
      "color": "Purple",
      "pieces": [                    // ordem = ordem na narrativa nova
        { "start_word_id": 140, "end_word_id": 160, "start": 88.2,  "end": 92.7 },
        { "start_word_id": 900, "end_word_id": 930, "start": 600.1, "end": 606.0 },
        { "start_word_id": 20,  "end_word_id": 28,  "start": 8.0,   "end": 10.5 }
      ]
    }
  ]
}
```

**Invariantes que o Core deve garantir (validação pydantic):**
- Todo `clip.start` == `words[start_word_id].start`; `clip.end` == `words[end_word_id].end`. Se a IA devolver timecode divergente, **ignora o timecode da IA** e recomputa por word ID.
- `end > start` para todo clip e piece.
- `start_word_id <= end_word_id`.
- Fronteiras de corte sempre em `word.start`/`word.end` (nunca no meio de palavra). Aplicar padding configurável (default 80 ms) e, se disponível, snap ao silêncio VAD mais próximo.

---

## 8. CONTRATOS DE INTERFACE (Core HTTP API)

Base: `http://127.0.0.1:8756`

| Método | Rota | Body / Params | Retorno |
|---|---|---|---|
| GET | `/health` | — | `{status, version, host_detected}` |
| GET | `/host` | — | `{host: "premiere"\|"davinci"\|"unknown", editor_version, studio: bool}` |
| POST | `/timeline/select` | `{}` (usa a ativa) | `{timeline_id, name, fps, duration_sec, tracks}` |
| POST | `/transcribe` | `{engine?, language?:"pt"}` | `{job_id}` (assíncrono) |
| GET | `/transcribe/{job_id}` | — | `{status, progress, words?, segments?}` |
| POST | `/clips/viral` | `{min_score?, max_clips?, target_duration_sec?}` | `{clips: [highlight...]}` |
| POST | `/clips/frankenbite` | `{n_variations?, max_duration_sec?}` | `{clips: [frankenbite...]}` |
| POST | `/clips/approve` | `{clip_ids: [...]}` | `{ok}` |
| POST | `/apply` | `{clip_ids: [...], mode:"new_timeline"\|"markers"}` | **DaVinci**: `{applied, new_timeline_name}`. **Premiere**: `{plan: PremiereCutPlan}` |
| POST | `/gaps/clean` | `{min_gap_sec?, track?}` | **DaVinci**: `{removed, new_timeline_name}`. **Premiere**: `{plan}` |
| GET | `/ui` | — | SPA |

**PremiereCutPlan** (o que o painel CEP recebe para materializar):
```jsonc
{
  "new_sequence_name": "Cortes Virais — [nome original]",
  "source_project_item_id": "…",     // item de origem já no projeto do Premiere
  "cuts": [
    { "id":"vir_001", "in_ticks":"104743680000000", "out_ticks":"116566...",
      "offset_sec": 0.0, "color_index": 2, "titulo":"O erro que 90% comete" }
  ]
}
```

---

## 9. AS FUNCIONALIDADES — especificação detalhada

### 9.1 Selecionar timeline + Transcrever

**Objetivo:** capturar o áudio da timeline ativa e produzir a transcrição word-level.

Fluxo:
1. UI chama `POST /timeline/select`. Adapter identifica a timeline/sequência ativa e devolve metadados (nome, fps, duração).
2. UI chama `POST /transcribe`.
3. Core pede ao Adapter que **exporte o áudio** da timeline:
   - **Premiere**: painel chama ExtendScript `exportSequenceAudio()` → `sequence.exportAsMediaDirect(path, eprAudioOnly, 0)` (0 = sequência inteira). Retorna o caminho.
   - **DaVinci**: Core usa Deliver — `project.SetRenderSettings({TargetDir, CustomName, ...})` + formato WAV + `AddRenderJob` + `StartRendering`, faz poll de `IsRenderingInProgress()`.
4. Core roda a engine de transcrição escolhida → preenche `words[]` e `segments[]` no `project_state.json`.
5. UI faz poll `GET /transcribe/{job_id}` até `status="done"`.

**Aceitação:** transcrição de um vídeo de 20 min em PT-BR com word timestamps, salva no state, visível na UI (texto corrido com marcação de tempo).

### 9.2 Extrair cortes virais (IA)

**Objetivo:** a IA lê a transcrição e propõe cortes "diretos" (um trecho contínuo que já é um corte viral).

Fluxo:
1. UI chama `POST /clips/viral` com preferências (nº máximo, duração alvo, score mínimo).
2. Core monta o prompt (Seção 12.1) passando os **segmentos com IDs** (não o áudio). Envia ao LLM com `temperature=0.1`.
3. LLM devolve JSON com, para cada corte: `start_word_id`, `end_word_id`, `titulo`, `motivo`, `score`, `hook_first_3s`. **Não devolve timecode** — o Core resolve por word ID.
4. Core valida (Seção 7), recomputa timecodes, snap a fronteiras de palavra + padding, grava em `clips[]`.
5. UI mostra os cortes como cards (título, score, duração, trecho de texto, botão aprovar).

**Critérios de viralidade que o prompt exige** (estilo Opus Clip — Hook/Flow/Trend):
- **Hook**: os primeiros ~3s prendem e conectam ao tema.
- **Flow**: narrativa completa dentro do corte (começo-meio-fim); dá pra entender sem contexto externo.
- **Emoção/contraintuição**: pico emocional, tensão, quebra de expectativa.
- Duração: configurável, default 20–90s.

**Aceitação:** de um vídeo de 20 min, a IA retorna 5–15 cortes ordenados por score, cada um com trecho de texto coerente e autossuficiente.

### 9.3 Aplicar cortes na timeline

**Objetivo:** materializar os cortes aprovados, separados e coloridos, **sem tocar a timeline original**.

**Design primário: nova timeline "Cortes Virais — [nome]".** Cada corte aprovado vira um clip separado nessa nova timeline, com cor distinta. (Justificativa: Seção 3 — é a única rota limpa e funciona nos dois editores.)

- **DaVinci** (`core/adapters/davinci.py`):
  1. Localiza o `MediaPoolItem` de origem (o vídeo da timeline original).
  2. Converte cada corte (`start`/`end` em segundos) para `startFrame`/`endFrame` na mídia de origem (`round(sec * fps)`; respeitar fps do projeto).
  3. Monta lista de `clipInfo` na ordem dos cortes.
  4. `MediaPool.CreateTimelineFromClips("Cortes Virais — …", clipInfos)`.
  5. Para cada `TimelineItem` retornado, `SetClipColor(cor)` e opcional `AddMarker(...)` com `customData=clip_id`.
  6. Verifica: `GetItemListInTrack` bate com o nº de cortes; `GetStart/GetEnd` coerentes.

- **Premiere** (`core/adapters/premiere_plan.py` calcula, painel materializa):
  1. Core devolve `PremiereCutPlan` (offsets/ticks/cor por corte).
  2. Painel ExtendScript (`applyCutPlan(plan)`):
     - Cria nova sequência (mesmo preset da original).
     - Para cada corte: `projectItem.createSubClip(name, inTicks, outTicks, hasHardBoundaries, takeVideo, takeAudio)` → **`subClip.setColorLabel(color_index)`** (funciona no projectItem!) → `newSeq.videoTracks[0].insertClip(subClip, offsetSec)`; áudio idem em audioTracks.
     - `offsetSec` acumula a duração de cada corte (encaixe sequencial).
  3. Painel relê a sequência e confirma nº de clips.

**Opção secundária (modo `markers`)**: em vez de nova timeline, cria **markers coloridos** nos pontos de corte da timeline original (Premiere: `sequence.markers`; DaVinci: `timeline.AddMarker`). Útil para quem quer navegar os cortes sem gerar nova timeline. Oferecer como toggle.

**Aceitação:** após "Aplicar", existe uma nova timeline/sequência com N clips separados e coloridos, tempos encaixados, e a original intacta. Testado nos dois editores.

### 9.4 Frankenbite — montagem de narrativa nova (FASE SEPARADA)

**Objetivo:** a IA cria cortes "melhores que o original" juntando falas de momentos diferentes numa narrativa coerente (gancho contraintuitivo → desenvolvimento → payoff), e **anexa ao final da timeline** já encaixado.

> Isto é tecnicamente um **supercut** (referência: videogrep). É mais arriscado que o corte direto — construir só depois que 9.1–9.3 estiverem sólidos.

Fluxo:
1. UI chama `POST /clips/frankenbite` (nº de variações).
2. Core monta o prompt (Seção 12.2): passa a lista de **frases com IDs** e pede uma **sequência ordenada de IDs de frases** que componha um arco. O LLM **não escreve timecodes nem reescreve o texto** — só ordena referências existentes e explica a coerência de cada transição.
3. Core valida rigorosamente:
   - Cada frase escolhida **existe literalmente** na transcrição (lookup por ID).
   - Fronteiras em `word.start`/`word.end` + padding; nada cortado no meio de palavra.
   - **Anti-redundância**: rejeita se duas peças têm alta similaridade semântica (dedupe).
   - **Anti-incoerência**: cada transição tem `coerencia_score`; abaixo do threshold → descarta a montagem ou a peça.
4. Materializa como um `clip` tipo `frankenbite` com `pieces[]`.
5. **Aplicar**: anexa ao **final da timeline original** (append), cada peça encaixada:
   - **DaVinci**: `AppendToTimeline([clipInfo...])` sem `recordFrame` (encaixe automático no fim) ou com `recordFrame` = fim atual. Cor `Purple`. (Se quiser não tocar a original, aplicar numa nova timeline "Frankenbite — …" via `CreateTimelineFromClips`.)
   - **Premiere**: inserir subclips sequenciais a partir do offset = duração atual da sequência.
   - Cross-fade de áudio curto (20–40 ms) nas junções para evitar corte seco (fase de polimento).

**Aceitação:** de um vídeo de 20 min, gerar ≥1 montagem coerente (sem falas sem sentido nem repetição), anexada no fim da timeline com cor distinta, tempos encaixados.

### 9.5 Limpar espaços (gaps) — FASE SEPARADA

**Objetivo:** numa timeline selecionada, identificar e remover os espaços vazios (gaps) entre clips. Opcional, independente das outras funções.

Fluxo:
1. UI chama `POST /gaps/clean` (opcional `min_gap_sec` = ignora gaps menores que X).
2. Core pede ao Adapter a lista de clips por track (start/end).
3. `core/gaps.py` detecta gaps: para clips ordenados por start, gap = `clip[n+1].start - clip[n].end > min_gap`.
4. Materialização:
   - **DaVinci**: **reconstruir contíguo** — não há remoção de gap nativa nem `SetStart`. Coletar `clipInfo` de cada item (`GetMediaPoolItem` + `GetLeftOffset`/`GetRightOffset`) e recriar timeline com `recordFrame` contíguo. Fazê-lo em **nova timeline** (não-destrutivo) ou substituir sob confirmação.
   - **Premiere**: mais frágil — não há "close gap" via API DOM. Opções: recriar sequência contígua (recomendado, mesma lógica do DaVinci) OU tentar QE (evitar). Preferir reconstrução.

**Aceitação:** timeline com 10 gaps → nova timeline sem gaps, mesma ordem de clips, duração reduzida pela soma dos gaps. Original intacta.

---

## 10. HOST ADAPTER — PREMIERE (referência de implementação)

**Stack:** Bolt CEP. Painel React consome a UI compartilhada e fala com o Core via `fetch`. Materialização via `CSInterface.evalScript`.

**Regras ExtendScript (aprendidas do fracasso do FastVideo):**
- Incluir `json2.jsx` (ES3 não tem JSON).
- Tudo cruza a ponte como **string**; `JSON.stringify` no JSX, `JSON.parse` no painel.
- `evalScript` é **síncrono no macOS, assíncrono no Windows** — sempre trabalhar pelo callback.
- **Nunca** usar `qe`/QE DOM. Nunca `trackItem.setColorLabel` (não existe).
- Ticks: `254016000000` ticks/segundo. Converter segundos→ticks e **snap ao frame boundary** do fps da sequência. Ticks como string.
- Processar cortes em **lotes** se forem muitos (evita payload gigante travando).

**Funções ExtendScript a implementar (`host/timeline.jsx`):**
```
getActiveSequenceInfo()        -> {id, name, fps, durationSec, videoTracks, audioTracks}
exportSequenceAudio(outPath, eprPresetPath) -> path        // exportAsMediaDirect(...,0)
applyCutPlan(planJsonString)   -> {ok, createdClips, seqName}
  // cria nova sequência; por corte: createSubClip -> setColorLabel(projectItem) -> insertClip
cleanGaps(trackType, minGapSec)-> {ok, newSeqName, removed}  // reconstrução contígua
addColoredMarkers(cutsJson)    -> {ok, count}                // modo markers
```

**Captura de áudio — abordagem adotada (melhor que .epr):** em vez de `exportAsMediaDirect` (que exige um `.epr` no disco e renderiza a sequência), usa-se `projectItem.getMediaPath()` para pegar o **arquivo de mídia de origem** e transcrevê-lo direto via ffmpeg (mesma técnica que o FastVideo já usa em produção nesta máquina). Vantagens: dispensa `.epr`, dispensa render, e os timecodes já saem em **tempo-de-origem** — exatamente o que `createSubClip` precisa, eliminando o mapeamento timeline→origem para o caso comum (um vídeo longo único). Função: `getSourceMediaPath()` (retorna path + nodeId + fps do clip principal). Ressalva: assume que o conteúdo relevante é o arquivo-fonte; timelines multi-fonte editadas usariam o fluxo de descritores de sequência (`getActiveSequenceInfo` + `build_cut_plan` com `SeqItemDesc`, também implementado).

**O que o painel faz:** detecta host (`/host`), renderiza a UI, e no botão "Aplicar" busca o `PremiereCutPlan` do Core e chama `applyCutPlan`.

---

## 11. HOST ADAPTER — DAVINCI (referência de implementação)

**Stack:** Python, dentro do Core. Requer **Resolve Studio** aberto. Env vars de scripting configuradas.

**Bootstrap:**
```python
import DaVinciResolveScript as dvr
resolve = dvr.scriptapp("Resolve")
pm = resolve.GetProjectManager()
project = pm.GetCurrentProject()
mediapool = project.GetMediaPool()
timeline = project.GetCurrentTimeline()
```

**Operações (`core/adapters/davinci.py`):**
```
get_active_timeline_info()   -> {id, name, fps, duration, tracks}
export_timeline_audio(path)  -> renderiza WAV via Deliver (SetRenderSettings/AddRenderJob/StartRendering + poll)
apply_cuts(clips, mode)      -> CreateTimelineFromClips + SetClipColor (+ AddMarker customData=clip_id)
append_frankenbite(pieces)   -> AppendToTimeline([clipInfo]) contíguo (ou nova timeline)
clean_gaps(track, min_gap)   -> reconstrói contíguo em nova timeline
```

**Conversão de tempo:** frames = `round(sec * fps)`. Respeitar `timeline.GetSetting("timelineFrameRate")`. Cuidado com 29.97 drop-frame. `clipInfo.startFrame/endFrame` referenciam a **mídia de origem**; `recordFrame` é destino na timeline — não confundir (erro nº1 da API).

**Transcrição:** preferir Whisper externo (word-level). `TranscribeAudio()` nativo é fallback (lê subtitle track via `GetItemListInTrack("subtitle", i)` → `GetName()`+`GetStart()`+`GetEnd()`), mas granularidade é de legenda, não de palavra.

**Segurança:** sempre `timeline.DuplicateTimeline(...)` antes de qualquer operação que pareça destrutiva. Não há undo confiável via API.

**UI no DaVinci (MVP):** o Core abre a UI num webview (pywebview) ou instrui o usuário a abrir `http://127.0.0.1:8756/ui`. Produto final: empacotar como Workflow Integration Plugin (Electron, Studio-only) que faz `require("WorkflowIntegration.node")`.

---

## 12. PROMPTS DE IA (completos)

### 12.1 Extração de cortes virais

**System:**
```
Você é um editor especialista em conteúdo viral para redes sociais (Reels, TikTok, Shorts).
Recebe a transcrição segmentada de um vídeo longo e identifica os melhores trechos para cortes virais.
Você NÃO escreve timestamps. Você referencia apenas os IDs de segmento/palavra fornecidos.
Responda SOMENTE com JSON válido no schema especificado. Sem prosa, sem markdown.
```

**User (template):**
```
VÍDEO: {titulo_ou_tema}
IDIOMA: {lang}
DURAÇÃO ALVO DOS CORTES: {min_dur}-{max_dur} segundos
Nº MÁXIMO DE CORTES: {max_clips}

TRANSCRIÇÃO (cada linha = um segmento com id, tempo e texto):
{para cada segmento: "[seg {id} | {start}-{end}] {text}"}

TAREFA:
Selecione os melhores trechos para cortes virais. Para cada corte, avalie:
- HOOK: os primeiros ~3s prendem a atenção e conectam ao tema?
- FLOW: o corte tem narrativa completa (começo-meio-fim) e se entende sem contexto externo?
- EMOÇÃO/CONTRAINTUIÇÃO: há pico emocional, tensão ou quebra de expectativa?

Cada corte deve começar e terminar em fronteiras de segmento (use os IDs).
Ordene por potencial viral (score 0-100).

RESPONDA NESTE SCHEMA (JSON puro):
{
  "clips": [
    {
      "start_seg_id": <int>,
      "end_seg_id": <int>,
      "titulo": "<string curta e chamativa>",
      "hook_first_3s": "<a frase de abertura do corte>",
      "motivo": "<por que viraliza, 1 frase>",
      "score": <int 0-100>
    }
  ]
}
Antes de responder, verifique: todo start_seg_id <= end_seg_id, todos os IDs existem, o JSON é válido.
```

> O Core converte `start_seg_id`→`segments[id].word_ids[0]`, `end_seg_id`→`segments[id].word_ids[-1]`, e daí para timecodes. A IA nunca vê nem escreve timecode bruto.
>
> **Nota estrutural (causa raiz 1.1):** repare que o schema acima **não tem campo `start`/`end` numérico em lugar nenhum** — só IDs inteiros. Isso não é estilo, é a correção estrutural do bug do FastVideo: se o campo não existe no schema, o LLM não tem como "copiar errado" um número. `core/viral.py` deve usar **saída estruturada com schema JSON estrito** (function calling / structured output da API, não JSON solto em texto) para tornar isso impossível de violar, não apenas instruído por prompt.

### 12.2 Frankenbite (montagem)

**System:**
```
Você é um roteirista de cortes que monta narrativas novas e poderosas juntando falas de momentos
diferentes de um vídeo. O resultado deve ser MELHOR que o corte linear: gancho contraintuitivo,
progressão lógica, payoff. Você só REORDENA frases que já existem — nunca inventa texto nem timestamps.
Proibido: falas sem sentido, redundância, transições incoerentes.
Responda SOMENTE JSON.
```

**User (template):**
```
IDIOMA: {lang}
DURAÇÃO MÁXIMA DA MONTAGEM: {max_dur}s
Nº DE VARIAÇÕES: {n}

FRASES DISPONÍVEIS (id | tempo | texto):
{para cada frase: "[{id} | {start}-{end}] {text}"}

TAREFA:
Crie {n} montagem(ns). Cada uma é uma SEQUÊNCIA ORDENADA de IDs de frase existentes, formando
um arco: gancho (contraintuitivo/curiosidade) → desenvolvimento → conclusão/payoff.
Para cada transição, explique por que ela é coerente e dê um coerencia_score (0-100).
Não repita a mesma ideia. Não escolha frases que só fazem sentido com contexto ausente.

SCHEMA (JSON puro):
{
  "montagens": [
    {
      "titulo": "<string>",
      "sequencia": [<id>, <id>, ...],
      "transicoes": [ {"de": <id>, "para": <id>, "motivo": "<1 frase>", "coerencia_score": <int>} ],
      "coerencia_geral": <int 0-100>
    }
  ]
}
Verifique antes de responder: todos os IDs existem, sem repetição de ideia, JSON válido.
```

> Core valida: existência dos IDs, `coerencia_geral >= threshold` (ex. 70), dedupe semântico das frases. Reprovados são descartados, não materializados.

---

## 13. UI/UX DA EXTENSÃO

**Filosofia:** 3 cliques para o resultado principal. Uma coluna, passos verticais.

Telas/estados:
1. **Início**: card da timeline ativa detectada (nome, duração, fps). Botão grande **"Analisar cortes virais"**. Link discreto: "Limpar espaços" e "Montagem frankenbite" (avançado).
2. **Transcrevendo**: barra de progresso + engine em uso (ex. "Whisper local").
3. **Cortes propostos**: lista de cards. Cada card: título, score (badge colorido), duração, trecho de texto (com o hook destacado), checkbox aprovar, preview do intervalo. Botão topo: "Selecionar todos / melhores". Botão fixo embaixo: **"Aplicar N cortes aprovados"** + toggle "Nova timeline / Marcadores".
4. **Aplicando**: progresso; ao fim, "✓ Nova timeline 'Cortes Virais — X' criada com N clips".
5. **Frankenbite** (avançado): botão "Gerar montagens" → lista de montagens com o roteiro (sequência de falas) e coerência → "Anexar ao final".
6. **Limpar espaços**: seleciona timeline → "Analisar gaps" → "Removi N gaps (−MM:SS)".

Regras de UX:
- Botões desabilitados com tooltip explicativo quando indisponível (ex.: DaVinci free → "Requer DaVinci Resolve Studio").
- Nunca travar a UI: transcrição/IA são assíncronas com progresso.
- Erros em linguagem humana + ação sugerida.
- **PT-BR** em toda a interface. Cuidado com acentuação (a marca é brasileira).

---

## 14. ESTRUTURA DE REPOSITÓRIO

```
viralcut/
├── core/                          # servidor Python (a inteligência)
│   ├── main.py                    # FastAPI app, rotas da Seção 8
│   ├── model.py                   # schemas pydantic (Seção 7)
│   ├── transcribe.py              # engines de transcrição (camadas)
│   ├── viral.py                   # LLM cortes virais (prompt 12.1)
│   ├── frankenbite.py             # LLM montagem (prompt 12.2)
│   ├── gaps.py                    # detecção de gaps (algoritmo puro)
│   ├── config.py                  # portas, chaves, engine padrão
│   └── adapters/
│       ├── base.py                # interface abstrata do adapter
│       ├── davinci.py             # Resolve Python API (materializa)
│       └── premiere_plan.py       # calcula PremiereCutPlan
├── ui/                            # SPA compartilhada (React+Vite ou HTML/JS)
│   ├── index.html
│   └── src/…
├── premiere-panel/                # Bolt CEP (empacota ui/ + bridge)
│   ├── CSXS/manifest.xml
│   ├── host/
│   │   ├── json2.jsx
│   │   └── timeline.jsx           # funções da Seção 10
│   └── presets/audio_only.epr
├── davinci-launcher/              # abre a UI no contexto do Resolve
│   └── launch.py                  # pywebview → http://127.0.0.1:8756/ui
├── tests/
│   ├── test_model.py              # invariantes do schema
│   ├── test_gaps.py               # detecção de gaps
│   ├── test_viral_parse.py        # parse/validação da saída do LLM
│   └── fixtures/                  # transcrições de exemplo
├── requirements.txt
├── README.md                      # instalação (Premiere + DaVinci Studio)
└── .env.example                   # OPENAI_API_KEY / ANTHROPIC_API_KEY, etc.
```

---

## 15. ROADMAP EM FASES (com critérios de aceitação)

> Construa nesta ordem. Não pule. Cada fase entrega valor sozinha.

**Fase 0 — Fundação (Core + detecção de host)**
- Core FastAPI sobe em `127.0.0.1:8756`; `/health` e `/host` funcionam.
- Modelo pydantic + testes de invariantes.
- ✅ Aceitação: `GET /health` responde; `test_model.py` verde.

**Fase 1 — Transcrição ponta a ponta (um editor primeiro: DaVinci)**
- Adapter DaVinci: detectar timeline ativa + exportar áudio WAV.
- Engine de transcrição (começar com OpenAI Whisper API, mais simples) → `words[]`/`segments[]`.
- ✅ Aceitação: selecionar timeline no Resolve Studio → transcrição PT-BR word-level no state.

**Fase 2 — Cortes virais (IA) + UI de aprovação**
- `POST /clips/viral` com prompt 12.1; validação por word ID; UI de cards.
- ✅ Aceitação: 20 min → 5–15 cortes coerentes, aprováveis na UI.

**Fase 3 — Aplicar cortes (DaVinci) — MVP COMPLETO**
- `apply_cuts` via `CreateTimelineFromClips` + `SetClipColor`. Verificação pós-aplicação.
- ✅ Aceitação: nova timeline "Cortes Virais — X" com N clips coloridos, original intacta. **Este é o produto mínimo vendável.**

**Fase 4 — Premiere (paridade do MVP)**
- Bolt CEP panel + `host/timeline.jsx` (`exportSequenceAudio`, `applyCutPlan`).
- Core calcula `PremiereCutPlan`; painel materializa (nova sequência + subclip + setColorLabel).
- ✅ Aceitação: mesmo fluxo das Fases 1-3 funcionando dentro do Premiere.

**Fase 5 — Transcrição local (custo zero)**
- whisper.cpp (Metal, Mac) ou WhisperX (GPU) com VAD; OpenAI vira fallback.
- ✅ Aceitação: transcrição local com word timestamps, sem custo por minuto.

**Fase 6 — Limpar espaços (gaps)**
- `gaps.py` + reconstrução contígua nos dois editores.
- ✅ Aceitação: timeline com gaps → nova timeline sem gaps, original intacta.

**Fase 7 — Frankenbite**
- Prompt 12.2 + validação anti-redundância/incoerência + append no fim.
- ✅ Aceitação: ≥1 montagem coerente anexada, sem falas sem sentido.

**Fase 8 — Polimento**
- Cross-fades de áudio nas junções; modo markers; export OTIO opcional; empacotamento (instalador Premiere ZXP/CEP + Workflow Integration Plugin DaVinci).

---

## 16. TRATAMENTO DE ERROS E EDGE CASES

| Caso | Comportamento esperado |
|---|---|
| Nenhuma timeline ativa | UI mostra "Abra uma timeline/sequência primeiro". Não falha. |
| DaVinci versão free | `/host` retorna `studio:false` → botões de aplicar desabilitados com aviso. |
| Áudio > 25 MB (Whisper API) | Core faz chunking com offset acumulado (somar tempo de início de cada chunk). |
| LLM devolve timecode divergente do word ID | Ignora o timecode do LLM, recomputa por ID. Loga aviso. |
| LLM devolve ID inexistente | Descarta o corte; se sobrar zero, avisa "IA não encontrou cortes, tente ajustar critérios". |
| Corte cairia no meio de palavra | Snap para `word.start`/`word.end` + padding (default 80 ms). |
| fps 29.97 drop-frame | Conversão tempo↔frame respeita DF; testar explicitamente. |
| Core não está rodando (painel Premiere) | Painel mostra "Inicie o VIRALCUT" + botão para abrir/instruções. |
| Falha no meio de aplicar | Como trabalhamos em timeline nova, a original está intacta; reportar e permitir retry. |
| Frankenbite incoerente (score baixo) | Não materializa; informa que nenhuma montagem passou no crivo. |
| Transcrição vazia (vídeo sem fala) | Avisa "Nenhuma fala detectada". |

---

## 17. TESTES E VALIDAÇÃO

- **Unit (Python)**: `model.py` (invariantes de word ID/timecode), `gaps.py` (detecção com fixtures), parse/validação da saída do LLM (mocks de resposta).
- **Integração DaVinci**: script que roda contra um projeto Resolve de teste (existe um em `Projetos/FASTVIDEO/PROJETO TESTE`) — aplicar cortes e reler a timeline para conferir contagem/cores.
- **Integração Premiere**: `applyCutPlan` num `.prproj` de teste; conferir nº de clips na sequência criada.
- **Teste de precisão de corte**: com um áudio conhecido, verificar que os cortes caem em fronteiras de palavra (±padding), não no meio.
- **Golden test do LLM**: fixture de transcrição → validar que a saída passa no schema e que os IDs existem (não valida "gosto", valida integridade).

---

## 18. RISCOS E MITIGAÇÕES

| Risco | Prob. | Impacto | Mitigação |
|---|---|---|---|
| Premiere depreca CEP (~2026) | Alta | Médio | Lógica de materialização isolada em `timeline.jsx`; migração futura a UXP/plugin híbrido C++ afeta só o adapter, não o Core |
| API do DaVinci muda entre versões | Média | Médio | Travar versão-alvo; testes de integração por versão; adapter isolado |
| Word-alignment impreciso em áudio ruim | Média | Médio | VAD + padding + snap a silêncio; permitir ajuste manual do corte na UI |
| LLM alucina cortes/IDs | Média | Baixo | Nunca confiar em timecode do LLM; validar IDs; temperature 0.1 |
| DaVinci free (usuário sem Studio) | Alta | Alto p/ esse usuário | Documentar requisito claramente; detectar e avisar |
| Custo de API (Whisper+LLM) escala | Baixa | Baixo | ~$0.15-0.25/vídeo na API; Fase 5 leva transcrição para local ($0) |
| Mac sem GPU NVIDIA (WhisperX não acelera) | Alta (é Mac) | Médio | whisper.cpp Metal como engine local no Mac; API como fallback |
| Frankenbite gera lixo incoerente | Média | Médio | Crivo duplo (coerência + dedupe); só materializa o que passa; é fase opcional |

---

## 19. CONFIGURAÇÃO E CREDENCIAIS

- `.env` (chmod 600, nunca versionado): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `VIRALCUT_LLM=anthropic|openai`, `VIRALCUT_TRANSCRIBE=api|whispercpp|whisperx`, `VIRALCUT_PORT=8756`.
- DaVinci env vars de scripting (`RESOLVE_SCRIPT_API`, `RESOLVE_SCRIPT_LIB`, `PYTHONPATH`) — documentar no README por SO.
- Nunca colocar chaves em código, na UI, ou em logs.

---

## 20. CHECKLIST DE EXECUÇÃO (para o próximo modelo)

- [ ] Confirmar versões: Premiere Pro 2025, DaVinci Resolve **Studio** 19/20.
- [ ] Fase 0: Core sobe, `/health` e `/host` ok, testes de modelo verdes.
- [ ] Fase 1: transcrição word-level no DaVinci ponta a ponta.
- [ ] Fase 2: cortes virais + UI de aprovação.
- [ ] Fase 3: aplicar cortes no DaVinci (nova timeline colorida). **← MVP**
- [ ] Fase 4: paridade no Premiere (Bolt CEP).
- [ ] Fase 5: transcrição local.
- [ ] Fase 6: limpar espaços.
- [ ] Fase 7: frankenbite.
- [ ] Fase 8: polimento + empacotamento.
- [ ] Em toda fase: operações de host verificadas relendo a timeline; original nunca tocada; UI em PT-BR sem erro de acentuação.

**Lembrete final:** o editor é o ator burro. A inteligência é o Core. A fonte de verdade é o word ID. A timeline original é sagrada. Foi ignorar essas quatro regras que matou o FastVideo.

---

*Fim do PLANO_MESTRE.md — VIRALCUT v1.0*
