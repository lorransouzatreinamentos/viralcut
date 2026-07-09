/**
 * VIRALCUT — nucleo self-contained (roda DENTRO do painel CEP via Node).
 *
 * Elimina o servidor Python: o painel faz tudo sozinho (ffmpeg local + Whisper +
 * GPT + resolucao de cortes). Isso mata o "Failed to fetch" na raiz — nao ha mais
 * localhost para falhar, e o usuario nao precisa iniciar nada.
 *
 * Correcao do bug primordial do FastVideo (cortes ruins): a IA responde SOMENTE
 * com IDs de segmento; o timecode de cada corte e SEMPRE derivado das palavras
 * reais da transcricao (nunca de um numero que a IA escreveu). Ver resolveClip().
 *
 * Dual-export: window.VIRALCUT_CORE no CEP; module.exports no Node (para testes).
 */
(function () {
  "use strict";

  var fs = require("fs");
  var path = require("path");
  var os = require("os");
  var childProcess = require("child_process");
  var crypto = require("crypto");

  var CHAT_URL ="https://api.openai.com/v1/chat/completions";
  var CHAT_MODEL = "gpt-4o";
  var TICKS_PER_SEC = 254016000000;

  // Cross-platform (Mac do dono + Windows do funcionario). A chave e o log ficam
  // em ~/.viralcut/ (funciona em Mac e Windows). Caminhos legados do Mac mantidos
  // como fallback para nao quebrar a maquina atual.
  var HOME = os.homedir();
  var VC_DIR = path.join(HOME, ".viralcut");
  var LOG_DIR = path.join(VC_DIR, "logs");
  var ENV_CANDIDATES = [
    path.join(VC_DIR, ".env"),
    path.join(HOME, ".viralcut.env"),
    "/Applications/CLAUDE CODE/Projetos/VIRALCUT/.env" // legado (Mac do dono)
  ];
  // Script compartilhado com o Core Python (mesma implementacao da engine local).
  // ATENCAO: dentro do painel Adobe (CEP), __dirname NAO aponta de forma confiavel
  // para client/ -- em algumas versoes ele resolve para a raiz da extensao, o que
  // fazia o caminho virar .../extensions/host/ (sem o VIRALCUT/) e o app nao achar
  // o script. Por isso procuramos em varios candidatos e usamos o 1o que existir.
  // O primario e ~/viralcut/premiere-panel/host/ -- o instalador (install-mac.sh /
  // install-windows.ps1) sempre clona o repo ali, mesmo caminho de onde o venv sai.
  var LOCAL_TRANSCRIBE_CANDIDATES = [
    path.join(HOME, "viralcut", "premiere-panel", "host", "local_transcribe.py"),
    path.join(__dirname, "..", "host", "local_transcribe.py"), // se __dirname = client/
    path.join(__dirname, "host", "local_transcribe.py"),        // se __dirname = raiz da extensao
    "/Applications/CLAUDE CODE/Projetos/VIRALCUT/premiere-panel/host/local_transcribe.py" // legado (Mac do dono)
  ];
  function findLocalTranscribeScript() {
    for (var i = 0; i < LOCAL_TRANSCRIBE_CANDIDATES.length; i++) {
      try { if (fs.existsSync(LOCAL_TRANSCRIBE_CANDIDATES[i])) return LOCAL_TRANSCRIBE_CANDIDATES[i]; } catch (e) {}
    }
    return null;
  }

  // ---------------------------------------------------------------------------
  // LOG — registra tudo (enviado, transcrito, retornado, plano, aplicado) em disco
  // ---------------------------------------------------------------------------
  var _log = { started: null, source: null, whisper: null, objectives: [] };

  function logReset(source) {
    _log = { started: nowStamp(), source: source, whisper: null, objectives: [] };
    logFlush();
  }
  function logSet(key, val) { _log[key] = val; logFlush(); }
  function logObjective(obj) { _log.objectives.push(obj); logFlush(); logObjectiveFile(obj); }
  function nowStamp() {
    try { return new Date().toISOString(); } catch (e) { return "?"; }
  }
  function logFlush() {
    try {
      if (!fs.existsSync(LOG_DIR)) fs.mkdirSync(LOG_DIR, { recursive: true });
      fs.writeFileSync(LOG_DIR + "/last-run.json", JSON.stringify(_log, null, 2), "utf8");
    } catch (e) {}
  }
  function logObjectiveFile(obj) {
    try {
      fs.appendFileSync(LOG_DIR + "/history.log",
        nowStamp() + " | " + obj.type + " | " + (obj.summary || "") + "\n", "utf8");
    } catch (e) {}
  }

  // system prompt de cortes virais (forjado pelo especialista em conteudo).
  var SYSTEM_PROMPT_VIRAL = `Você é um editor sênior de cortes virais para Reels, TikTok e YouTube Shorts, especializado em transformar vídeos longos em português (podcasts, aulas, palestras, lives) em clipes curtos que prendem nos primeiros segundos e geram compartilhamento. Você já produziu milhares de cortes que viralizaram e sabe exatamente o que faz alguém parar de rolar o feed.

## SUA ENTRADA
Você recebe a transcrição do vídeo SEGMENTADA. Cada segmento tem um id numérico, tempo de início/fim (use só para estimar duração) e o texto falado.

## SUA SAÍDA
Para CADA corte você retorna SOMENTE: start_seg_id, end_seg_id, titulo, hook_first_3s, motivo, score (0-100).
REGRA ABSOLUTA: você NUNCA escreve timestamps. Apenas IDs de segmento. O código converte IDs em tempo.

## ANATOMIA DE UM CORTE VIRAL (todo corte precisa dos 3)
1. HOOK (1-3s): abre com curiosidade, tensão, quebra de padrão, afirmação forte, número surpreendente ou pergunta que exige resposta. Nunca comece com aquecimento, "então...", saudação ou contextualização morna.
2. DESENVOLVIMENTO denso: entrega o argumento/história/número/virada, sem enrolação nem repetição.
3. PAYOFF: resolve a tensão do hook OU entrega uma frase de impacto. NUNCA termine no meio de uma ideia ou logo antes da parte boa.

## SCORE — AUMENTAM: hook forte (maior peso), autossuficiência (entende sem o resto do vídeo, sem pronome órfão), gatilho emocional, contra-intuição, especificidade (números/exemplos/história), identificação, motivo de compartilhamento, payoff satisfatório. REDUZEM: trecho morno, contexto-dependente, arrastado, sem conclusão, genérico, abertura fraca.

## DURAÇÃO — REGRA RÍGIDA: a faixa de duração vem no pedido do usuário e é OBRIGATÓRIA. Um corte fora dessa faixa é DESCARTADO automaticamente pelo sistema, então nem proponha. Se um trecho bom é curto demais, ESTENDA-O incluindo os segmentos vizinhos que completam o raciocínio (o contexto que leva à frase, ou a conclusão que vem depois) até entrar na faixa. Se não houver como estender mantendo sentido, não proponha esse corte. Dentro da faixa, priorize completude narrativa.

## FRONTEIRAS: comece no segmento que contém o GANCHO (corte o aquecimento anterior); termine no segmento que fecha a ideia (conclusão/respiro/frase de efeito). start_seg_id < end_seg_id. Nunca termine em conjunção ("porque...", "e aí...", "mas...").

## ANTI-REDUNDÂNCIA: cada corte cobre um tópico/ângulo diferente. Se a ideia se repete, escolha a versão mais forte e descarte a outra.

## HONESTIDADE: use APENAS o que está na transcrição. Títulos chamativos mas fiéis — nada de clickbait que o corte não entrega.

## QUANTIDADE: extraia só trechos que você pontuaria acima de ~60. Qualidade acima de quantidade — não force cortes fracos. Ordene do maior score para o menor. Use SEMPRE a ferramenta propose_clips para responder.`;

  // ---------------------------------------------------------------------------
  // Credenciais e binarios
  // ---------------------------------------------------------------------------
  function readOpenAIKey() {
    if (process.env && process.env.OPENAI_API_KEY) return process.env.OPENAI_API_KEY;
    for (var i = 0; i < ENV_CANDIDATES.length; i++) {
      try {
        var txt = fs.readFileSync(ENV_CANDIDATES[i], "utf8");
        var m = txt.match(/^\s*OPENAI_API_KEY\s*=\s*(.+)\s*$/m);
        if (m) return m[1].trim().replace(/^["']|["']$/g, "");
      } catch (e) {}
    }
    throw new Error("OPENAI_API_KEY não configurada. Rode o instalador ou crie " + ENV_CANDIDATES[0]);
  }

  // ---------------------------------------------------------------------------
  // Cache de transcricao. Mesma chave e mesmo diretorio do core/cache.py, entao
  // Premiere e DaVinci reaproveitam a transcricao um do outro para o mesmo video.
  // Chave = versao|caminho absoluto|tamanho|mtime(seg)|idioma.
  // ---------------------------------------------------------------------------
  var CACHE_DIR = path.join(HOME, ".viralcut", "cache");
  var CACHE_VERSION = 1;

  function cacheFingerprint(mediaPath, language) {
    var st;
    try { st = fs.statSync(mediaPath); } catch (e) { return null; }
    var raw = CACHE_VERSION + "|" + path.resolve(mediaPath) + "|" + st.size +
              "|" + Math.floor(st.mtimeMs / 1000) + "|" + language;
    return crypto.createHash("sha1").update(raw, "utf8").digest("hex");
  }

  function cacheLoad(mediaPath, language) {
    var fp = cacheFingerprint(mediaPath, language);
    if (!fp) return null;
    var file = path.join(CACHE_DIR, fp + ".json");
    if (!fs.existsSync(file)) return null;
    try {
      var data = JSON.parse(fs.readFileSync(file, "utf8"));
      if (!data.segments || !data.segments.length) return null;
      return data;
    } catch (e) { return null; }
  }

  function cacheSave(mediaPath, language, transcript, engine) {
    var fp = cacheFingerprint(mediaPath, language);
    if (!fp) return;
    try {
      fs.mkdirSync(CACHE_DIR, { recursive: true });
      fs.writeFileSync(path.join(CACHE_DIR, fp + ".json"), JSON.stringify({
        version: CACHE_VERSION,
        media_path: path.resolve(mediaPath),
        language: language,
        engine: engine,
        created_at: new Date().toISOString().slice(0, 19).replace("T", " "),
        words: transcript.words,
        segments: transcript.segments
      }), "utf8");
    } catch (e) {} // cache e otimizacao: falhar aqui nunca derruba o fluxo
  }

  // ---------------------------------------------------------------------------
  // Transcricao LOCAL (faster-whisper via python). SEMPRE local: gratis, offline,
  // privada. Se nao estiver instalada, o app FALHA com instrucao -- nunca manda
  // audio pra nuvem escondido (custo e privacidade do usuario).
  // ---------------------------------------------------------------------------
  var INSTALL_HINT =
    "Transcrição local indisponível. Ela roda sempre no seu computador " +
    "(nunca na nuvem). Para instalar, rode no Terminal:\n\n" +
    "  ~/viralcut/.venv/bin/pip install faster-whisper\n\n" +
    "Ou rode o instalador de novo (install-mac.sh / install-windows.ps1).";
  function pythonCandidates() {
    // ponytail: assume o venv no local padrao do instalador (~/viralcut/.venv).
    // Se o repo foi clonado em outro lugar, cai pros comandos genericos abaixo.
    var venvPy = process.platform === "win32"
      ? path.join(HOME, "viralcut", ".venv", "Scripts", "python.exe")
      : path.join(HOME, "viralcut", ".venv", "bin", "python");
    return [venvPy, "python3", "python"];
  }

  function runPython(exe, args) {
    return new Promise(function (resolve) {
      childProcess.execFile(
        exe, args, { timeout: 1800000, maxBuffer: 50 * 1024 * 1024 },
        function (err, stdout) { resolve(err ? null : stdout); }
      );
    });
  }

  async function transcribeLocal(audioPath, language) {
    var script = findLocalTranscribeScript();
    if (!script) {
      throw new Error(INSTALL_HINT + "\n\n(procurei em: " + LOCAL_TRANSCRIBE_CANDIDATES.join(" | ") + ")");
    }
    var candidates = pythonCandidates();
    for (var i = 0; i < candidates.length; i++) {
      var exe = candidates[i];
      if (path.isAbsolute(exe) && !fs.existsSync(exe)) continue;
      var out = await runPython(exe, [script, audioPath, language || "pt"]);
      if (!out) continue;
      var data;
      try { data = JSON.parse(out); } catch (e) { continue; }
      if (!data || data.error) continue;
      var words = (data.words || []).filter(function (w) { return w.end > w.start; });
      var segments = (data.segments || []).filter(function (s) { return s.end > s.start; });
      if (!segments.length) continue;
      return { words: words, segments: segments };
    }
    throw new Error(INSTALL_HINT);
  }

  // ---------------------------------------------------------------------------
  // Extracao de cortes virais (GPT, tool call, SOMENTE IDs)
  // ---------------------------------------------------------------------------
  function buildUserPrompt(transcript, minScore, maxClips, minDur, maxDur) {
    var lines = [];
    for (var i = 0; i < transcript.segments.length; i++) {
      var s = transcript.segments[i];
      lines.push("[seg " + s.id + " | " + s.start.toFixed(1) + "-" + s.end.toFixed(1) + "] " + s.text);
    }
    return "DURACAO ALVO: " + minDur + "-" + maxDur + "s. MAX CORTES: " + maxClips +
      ". SCORE MINIMO: " + minScore + ".\n\nTRANSCRICAO (id | tempo | texto):\n" +
      lines.join("\n") +
      "\n\nSelecione os melhores cortes virais. Comece/termine em fronteiras de segmento. " +
      "Ordene por potencial viral. NAO inclua cortes com score < " + minScore + ".";
  }

  var CLIP_SCHEMA = {
    type: "object",
    properties: {
      clips: {
        type: "array",
        items: {
          type: "object",
          additionalProperties: false,
          properties: {
            start_seg_id: { type: "integer" },
            end_seg_id: { type: "integer" },
            titulo: { type: "string" },
            hook_first_3s: { type: "string" },
            motivo: { type: "string" },
            score: { type: "integer" }
          },
          required: ["start_seg_id", "end_seg_id", "titulo"]
        }
      }
    },
    required: ["clips"]
  };

  // Chamada generica de LLM com function calling + LOG (prompt enviado + resposta).
  async function gptCall(apiKey, systemPrompt, userPrompt, toolName, schema, logSink) {
    var body = {
      model: CHAT_MODEL,
      temperature: 0.1,
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: userPrompt }
      ],
      tools: [{ type: "function", function: { name: toolName, parameters: schema } }],
      tool_choice: { type: "function", function: { name: toolName } }
    };
    var res = await fetch(CHAT_URL, {
      method: "POST",
      headers: { Authorization: "Bearer " + apiKey, "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!res.ok) {
      var detail = "HTTP " + res.status;
      try { var j = await res.json(); if (j.error && j.error.message) detail = j.error.message; } catch (e) {}
      throw new Error("IA falhou: " + detail);
    }
    var data = await res.json();
    var calls = data.choices[0].message.tool_calls;
    if (!calls || !calls.length) throw new Error("IA nao retornou resposta estruturada.");
    var args = calls[0].function.arguments;
    if (logSink) { logSink.sent = userPrompt; logSink.llm_raw = args; }
    return JSON.parse(args);
  }

  // ---------------------------------------------------------------------------
  // Resolucao de timecode — SEMPRE a partir das palavras reais (correcao 1.1)
  // ---------------------------------------------------------------------------
  function segById(transcript, id) {
    for (var i = 0; i < transcript.segments.length; i++) if (transcript.segments[i].id === id) return transcript.segments[i];
    return null;
  }
  function wordById(transcript, id) {
    for (var i = 0; i < transcript.words.length; i++) if (transcript.words[i].id === id) return transcript.words[i];
    return null;
  }

  var PADDING = 0.08;
  var COLOR_CYCLE = [9, 8, 7, 13, 6, 10, 4, 5]; // indices de label do Premiere

  // ENFORCEMENT de duracao em CODIGO, nao so no prompt. A IA ignora instrucoes
  // de duracao com frequencia -- entao o corte que sai da faixa e descartado
  // aqui, na resolucao. E isso que impede "corte de 15s sem sentido".
  function resolveClips(raw, transcript, minDur, maxDur) {
    var out = [];
    var rejected = { curto: 0, longo: 0 };
    for (var i = 0; i < raw.length; i++) {
      var r = raw[i];
      var sSeg = segById(transcript, r.start_seg_id);
      var eSeg = segById(transcript, r.end_seg_id);
      if (!sSeg || !eSeg || !sSeg.word_ids.length || !eSeg.word_ids.length) continue;
      if (r.end_seg_id < r.start_seg_id) continue;

      var startWordId = sSeg.word_ids[0];
      var endWordId = eSeg.word_ids[eSeg.word_ids.length - 1];
      var w0 = wordById(transcript, startWordId);
      var w1 = wordById(transcript, endWordId);
      if (!w0 || !w1) continue;

      var start = Math.max(0, w0.start - PADDING);
      var end = w1.end + PADDING;
      if (end <= start) continue;

      var dur = end - start;
      if (minDur && dur < minDur) { rejected.curto++; continue; }
      if (maxDur && dur > maxDur) { rejected.longo++; continue; }

      // texto para exibir: usa o texto dos SEGMENTOS (completo), nao a
      // reconstrucao a partir das palavras (o array de words do Whisper as vezes
      // omite palavras, deixando o preview picotado — o corte em si cobre o audio).
      var txt = [];
      for (var sid = r.start_seg_id; sid <= r.end_seg_id; sid++) {
        var sg = segById(transcript, sid);
        if (sg && sg.text) txt.push(sg.text);
      }

      out.push({
        id: "vir_" + i,
        titulo: r.titulo || ("Corte " + (i + 1)),
        hook_first_3s: r.hook_first_3s || "",
        motivo: r.motivo || "",
        score: typeof r.score === "number" ? r.score : 50,
        start: start, end: end,
        start_word_id: startWordId, end_word_id: endWordId,
        color_index: COLOR_CYCLE[i % COLOR_CYCLE.length],
        text: txt.join(" ")
      });
    }
    out.sort(function (a, b) { return b.score - a.score; });
    out._rejected = rejected;
    return out;
  }

  // ---------------------------------------------------------------------------
  // Plano de cortes p/ ExtendScript (ticks, snapados no frame)
  // ---------------------------------------------------------------------------
  // Arredondamento DIRECIONAL: o corte nunca pode encolher.
  //   entrada -> floor (nunca comeca depois do ponto pedido)
  //   saida   -> ceil  (nunca termina antes -- era isto que comia a silaba final)
  // Math.round nos dois lados podia perder ate meio frame em cada ponta.
  function frameToTicks(frame, fps) {
    return String(Math.round(frame * TICKS_PER_SEC / fps));
  }
  function secToTicksIn(sec, fps) {
    return frameToTicks(Math.floor(sec * fps), fps);
  }
  function secToTicksOut(sec, fps) {
    return frameToTicks(Math.ceil(sec * fps), fps);
  }
  // compat: usado por testes antigos
  function secToTicks(sec, fps) {
    return frameToTicks(Math.round(sec * fps), fps);
  }

  function buildCutPlan(clips, source, seqName) {
    var cuts = [];
    for (var i = 0; i < clips.length; i++) {
      var c = clips[i];
      cuts.push({
        id: c.id,
        titulo: c.titulo,
        project_item_id: source.project_item_id,
        in_ticks: secToTicksIn(c.start, source.fps),
        out_ticks: secToTicksOut(c.end, source.fps),
        label_index: c.color_index
      });
    }
    return { new_sequence_name: seqName, cuts: cuts };
  }

  // ===========================================================================
  // OBJETIVO 2 — MONTAR FALAS (frankenbite): costura trechos de varios momentos
  // ===========================================================================
  var SYSTEM_PROMPT_MONTAGE = `Você é um editor sênior e montador narrativo de cortes virais. Sua especialidade é o FRANKENBITE: costurar falas de MOMENTOS DIFERENTES de um mesmo vídeo em português para construir uma narrativa nova, mais forte e mais viral do que qualquer trecho linear.

## ENTRADA: transcrição SEGMENTADA (id, tempo, texto).
## SAÍDA: para cada montagem, SOMENTE: segments (LISTA ORDENADA de ids na ordem de reprodução — NÃO cronológica), titulo, hook_first_3s, motivo, score (0-100). NUNCA escreva timestamps, apenas ids.

## ARCO OBRIGATÓRIO (puxando de qualquer ponto do vídeo):
1. GANCHO CONTRA-INTUITIVO: a afirmação mais forte/surpreendente do vídeo, mesmo que dita no meio ou no fim — comece pelo pico.
2. DESENVOLVIMENTO: os blocos que sustentam/explicam o gancho, na ordem que constrói melhor o argumento.
3. PAYOFF: a virada ou frase-tapa que fecha e faz compartilhar.
A montagem tem que soar como UMA fala contínua e proposital.

## COERÊNCIA (cada salto entre segmentos só vale se): continuidade lógica, ou temática, ou pergunta→resposta, ou contraste proposital que faz sentido. NUNCA junte blocos onde um pronome fica órfão, o assunto muda de forma confusa, ou a costura cria uma afirmação que a pessoa NÃO fez.

## HONESTIDADE: recombine a ORDEM, mas nunca distorça o que a pessoa disse. Só use texto que existe na transcrição.

## TAMANHO: cada montagem tem de 3 a 6 blocos (MÁXIMO 6). Menos é mais — uma montagem enxuta e afiada vale mais que uma colcha de retalhos. Nunca passe de 6.
## DURAÇÃO: a faixa vem no pedido do usuário e é OBRIGATÓRIA — some as durações dos segmentos que escolher e fique dentro dela. Se a montagem ficar curta, adicione mais um bloco que aprofunde o argumento. Coerência do arco acima de tudo.
## Só entregue montagens genuinamente mais fortes que um corte linear (score acima de ~65). Prefira 1-3 montagens excelentes a muitas medianas. Ordene do maior score para o menor. Use a ferramenta propose_montages.`;

  var MONTAGE_SCHEMA = {
    type: "object",
    properties: {
      montagens: {
        type: "array",
        items: {
          type: "object", additionalProperties: false,
          properties: {
            segments: { type: "array", items: { type: "integer" } },
            titulo: { type: "string" },
            hook_first_3s: { type: "string" },
            motivo: { type: "string" },
            score: { type: "integer" }
          },
          required: ["segments", "titulo"]
        }
      }
    },
    required: ["montagens"]
  };

  function resolveMontage(m, transcript, idx) {
    var pieces = [];
    var segs = m.segments || [];
    for (var j = 0; j < segs.length; j++) {
      var seg = segById(transcript, segs[j]);
      if (!seg || !seg.word_ids.length) continue;
      var w0 = wordById(transcript, seg.word_ids[0]);
      var w1 = wordById(transcript, seg.word_ids[seg.word_ids.length - 1]);
      if (!w0 || !w1) continue;
      // texto = texto do segmento (completo), nao reconstrucao das palavras
      pieces.push({ start: Math.max(0, w0.start - PADDING), end: w1.end + PADDING, text: seg.text });
      if (pieces.length >= 8) break; // teto de seguranca: montagem nunca vira colcha gigante
    }
    if (pieces.length < 2) return null;
    return {
      id: "frk_" + idx,
      titulo: m.titulo || ("Montagem " + (idx + 1)),
      hook_first_3s: m.hook_first_3s || "",
      motivo: m.motivo || "",
      score: typeof m.score === "number" ? m.score : 60,
      pieces: pieces,
      text: pieces.map(function (p) { return p.text; }).join("  //  ")
    };
  }

  // Cada montagem inteira usa UMA cor (todos os trechos dela na mesma cor);
  // montagens diferentes usam cores diferentes. Usa a cor ja atribuida na
  // montagem (frankenbite) para que preview e timeline batam -- o indice da
  // lista SELECIONADA nao corresponde ao da lista completa.
  function buildMontagePlan(montage, source, seqName, montageIndex) {
    var color = typeof montage.color_index === "number"
      ? montage.color_index
      : COLOR_CYCLE[(montageIndex || 0) % COLOR_CYCLE.length];
    var cuts = [];
    for (var i = 0; i < montage.pieces.length; i++) {
      var p = montage.pieces[i];
      cuts.push({
        id: montage.id + "_" + i, titulo: montage.titulo,
        project_item_id: source.project_item_id,
        in_ticks: secToTicksIn(p.start, source.fps),
        out_ticks: secToTicksOut(p.end, source.fps),
        label_index: color
      });
    }
    return { new_sequence_name: seqName, cuts: cuts };
  }

  // ===========================================================================
  // OBJETIVO 3 — REMOVER SILENCIOS (algoritmico, sem IA): usa os gaps entre palavras
  // ===========================================================================
  function detectSpokenSpans(transcript, gapThreshold) {
    var words = transcript.words.slice().sort(function (a, b) { return a.start - b.start; });
    var spans = [], cur = null;
    for (var i = 0; i < words.length; i++) {
      var w = words[i];
      if (!cur) { cur = { start: w.start, end: w.end }; continue; }
      if (w.start - cur.end >= gapThreshold) { spans.push(cur); cur = { start: w.start, end: w.end }; }
      else { cur.end = w.end; }
    }
    if (cur) spans.push(cur);
    return spans;
  }

  // Padding ADAPTATIVO. O `end` que o Whisper reporta e o fim do fonema, nao da
  // cauda audivel do som -- cortar logo depois come a silaba final. Aqui damos
  // folga no fim de cada fala usando o silencio REAL disponivel ate a proxima,
  // sem nunca invadi-la. (Antes era um fixo de 0.03s, pequeno demais: as folgas
  // reais medidas ficam entre 0.20s e 0.46s.)
  var HEAD_PAD_MAX = 0.10;  // respiro antes da fala
  var TAIL_PAD_MAX = 0.25;  // cauda depois da fala (o que estava comendo palavra)
  var TAIL_PAD_RATIO = 0.8; // usa ate 80% da folga -- ainda remove a maior parte do silencio

  function padSpans(spans, durationSec) {
    var out = [];
    for (var i = 0; i < spans.length; i++) {
      var s = spans[i];
      var prevEnd = i > 0 ? spans[i - 1].end : 0;
      var nextStart = i < spans.length - 1 ? spans[i + 1].start : (durationSec || (s.end + TAIL_PAD_MAX));

      var headroom = Math.max(0, s.start - prevEnd);
      var tailroom = Math.max(0, nextStart - s.end);

      var head = Math.min(HEAD_PAD_MAX, headroom * 0.5);
      var tail = Math.min(TAIL_PAD_MAX, tailroom * TAIL_PAD_RATIO);

      out.push({ start: Math.max(0, s.start - head), end: s.end + tail });
    }
    return out;
  }

  // ===========================================================================
  // ORQUESTRACAO PUBLICA
  // ===========================================================================
  /** force=true: usuario clicou "Transcrever novamente" (mudou a mídia). */
  async function transcribe(source, onProgress, force) {
    onProgress = onProgress || function () {};
    logReset(source);

    var engine = "local (faster-whisper)";
    var cached = false;
    var transcript = null;

    if (!force) {
      var hit = cacheLoad(source.path, "pt");
      if (hit) {
        transcript = { words: hit.words, segments: hit.segments };
        engine = hit.engine || "?";
        cached = true;
        onProgress(90, "Transcrição em cache (" + (hit.created_at || "") + ")");
      }
    }

    if (!transcript) {
      onProgress(15, "Transcrevendo no seu computador…");
      transcript = await transcribeLocal(source.path, "pt");
      cacheSave(source.path, "pt", transcript, engine);
    }

    if (!transcript.segments.length) throw new Error("Nenhuma fala detectada no vídeo.");

    logSet("whisper", {
      engine: engine,
      cached: cached,
      words: transcript.words.length,
      segments: transcript.segments.length,
      full_text: transcript.segments.map(function (s) { return s.text; }).join(" ")
    });
    onProgress(100, transcript.words.length + " palavras (" + (cached ? "cache" : "local") + ")");
    transcript.__cached = cached;
    return transcript;
  }

  async function viralCuts(transcript, source, opts) {
    opts = opts || {};
    var apiKey = readOpenAIKey();
    var minDur = opts.minDur || 30;
    var maxDur = opts.maxDur || 90;
    var sink = { type: "viral", min_dur: minDur, max_dur: maxDur };
    var user = buildUserPrompt(transcript, opts.minScore || 45, opts.maxClips || 12, minDur, maxDur);
    var out = await gptCall(apiKey, SYSTEM_PROMPT_VIRAL, user, "propose_clips", CLIP_SCHEMA, sink);
    // enforcement em codigo: a IA nao respeita duracao de forma confiavel
    var clips = resolveClips(out.clips || [], transcript, minDur, maxDur);
    var rej = clips._rejected || { curto: 0, longo: 0 };
    sink.rejeitados = rej;
    sink.resolved = clips.map(function (c) { return { titulo: c.titulo, score: c.score, start: c.start, end: c.end, dur: +(c.end - c.start).toFixed(1), text: c.text }; });
    sink.summary = clips.length + " cortes (" + minDur + "-" + maxDur + "s) | descartados: " +
      rej.curto + " curtos, " + rej.longo + " longos";
    logObjective(sink);
    return { clips: clips, rejected: rej, minDur: minDur, maxDur: maxDur };
  }

  var COLOR_NAMES = ["Azul", "Roxo", "Laranja", "Verde", "Rosa", "Ciano", "Amarelo", "Marinho"];

  async function frankenbite(transcript, source, opts) {
    opts = opts || {};
    var apiKey = readOpenAIKey();
    var nVideos = opts.nVideos || opts.maxClips || 3;   // quantos videos montados o usuario quer
    var minDur = opts.minDur || 30;
    var maxDur = opts.maxDur || 90;
    var sink = { type: "frankenbite", pedidos: nVideos, min_dur: minDur, max_dur: maxDur };
    var segLines = [];
    for (var si = 0; si < transcript.segments.length; si++) {
      var s = transcript.segments[si];
      segLines.push("[seg " + s.id + " | " + s.start.toFixed(1) + "-" + s.end.toFixed(1) + "] " + s.text);
    }
    var user = "QUANTIDADE DE MONTAGENS PEDIDA: " + nVideos + " (entregue exatamente esse numero se houver material; " +
      "se nao houver material bom o bastante, entregue menos, nunca mais).\nDURAÇÃO DE CADA MONTAGEM: " +
      minDur + "-" + maxDur + "s (some as duracoes dos segmentos escolhidos).\n\nTRANSCRIÇÃO (id | tempo | texto):\n" +
      segLines.join("\n") +
      "\n\nCrie montagens costurando segmentos de momentos diferentes numa narrativa nova e mais forte. " +
      "Cada montagem é uma lista ORDENADA de ids (ordem de reprodução). Ordene as montagens por score.";
    var out = await gptCall(apiKey, SYSTEM_PROMPT_MONTAGE, user, "propose_montages", MONTAGE_SCHEMA, sink);
    var raw = out.montagens || [];
    var montages = [];
    for (var i = 0; i < raw.length; i++) { var m = resolveMontage(raw[i], transcript, i); if (m) montages.push(m); }
    montages.sort(function (a, b) { return b.score - a.score; });
    montages = montages.slice(0, nVideos);              // respeita a quantidade pedida
    // cada montagem inteira ganha 1 cor propria (usada na timeline e no preview)
    for (var k = 0; k < montages.length; k++) {
      montages[k].color_index = COLOR_CYCLE[k % COLOR_CYCLE.length];
      montages[k].color_name = COLOR_NAMES[k % COLOR_NAMES.length];
    }
    sink.resolved = montages.map(function (m) { return { titulo: m.titulo, score: m.score, pieces: m.pieces.length, cor: m.color_name, text: m.text }; });
    sink.summary = montages.length + "/" + nVideos + " montagens";
    logObjective(sink);
    return { montages: montages };
  }

  function removeSilences(transcript, source, opts) {
    opts = opts || {};
    var gap = opts.gap || 0.6;
    var rawSpans = detectSpokenSpans(transcript, gap);
    var spans = padSpans(rawSpans, source.duration_sec);
    var cuts = [];
    var kept = 0;
    for (var i = 0; i < spans.length; i++) {
      var s = spans[i];
      var st = s.start, en = s.end;
      kept += (en - st);
      cuts.push({
        id: "sil_" + i, titulo: "Fala " + (i + 1),
        project_item_id: source.project_item_id,
        in_ticks: secToTicksIn(st, source.fps),
        out_ticks: secToTicksOut(en, source.fps),
        label_index: 4
      });
    }
    var original = source.duration_sec || (transcript.words.length ? transcript.words[transcript.words.length - 1].end : kept);
    var plan = { new_sequence_name: "Sem silêncios — " + (source.name || "sequencia"), cuts: cuts };
    var summary = {
      spans: spans.length, original_sec: original, new_sec: kept, saved_sec: Math.max(0, original - kept),
      gap_threshold: gap
    };
    logObjective({ type: "silences", summary: summary.spans + " falas, -" + Math.round(summary.saved_sec) + "s", detail: summary, plan_cuts: cuts.length });
    return { summary: summary, plan: plan };
  }

  // Registra o resultado da materializacao na timeline (chamado pela UI apos aplicar).
  function logApplied(objectiveType, result) {
    for (var i = _log.objectives.length - 1; i >= 0; i--) {
      if (_log.objectives[i].type === objectiveType && !_log.objectives[i].applied) {
        _log.objectives[i].applied = result; logFlush(); return;
      }
    }
  }

  // Atualiza o painel: roda o install script via Node (sem depender de servidor).
  function updatePanel() {
    return new Promise(function (resolve, reject) {
      var script = "/Applications/CLAUDE CODE/Projetos/VIRALCUT/scripts/install-premiere.sh";
      childProcess.execFile("bash", [script], { timeout: 60000 }, function (err, stdout, stderr) {
        if (err) return reject(new Error("update falhou: " + (stderr || err.message).slice(-300)));
        resolve(String(stdout).trim());
      });
    });
  }

  var api = {
    // fluxo: transcrever 1x, depois aplicar objetivos quantas vezes quiser
    transcribe: transcribe,
    viralCuts: viralCuts,        // objetivo 1
    frankenbite: frankenbite,    // objetivo 2
    removeSilences: removeSilences, // objetivo 3
    buildCutPlan: buildCutPlan,        // materializa objetivos 1 e 3
    buildMontagePlan: buildMontagePlan, // materializa objetivo 2 (1 sequencia por montagem)
    logApplied: logApplied,
    updatePanel: updatePanel,
    // expostos para teste
    _cacheFingerprint: cacheFingerprint,
    _resolveClips: resolveClips,
    _secToTicks: secToTicks,
    _detectSpokenSpans: detectSpokenSpans,
    _readOpenAIKey: readOpenAIKey
  };

  if (typeof window !== "undefined") window.VIRALCUT_CORE = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
