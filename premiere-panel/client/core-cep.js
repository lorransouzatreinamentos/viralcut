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

  var WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions";
  var CHAT_URL = "https://api.openai.com/v1/chat/completions";
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
  var FFMPEG_CANDIDATES = [
    "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg",
    "C:\\ffmpeg\\bin\\ffmpeg.exe", "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe"
  ];

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

## DURAÇÃO: ~15 a ~90s. Priorize SEMPRE completude narrativa sobre duração exata. Um corte de 22s completo vale mais que 60s que corta no meio.

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

  function findFfmpeg() {
    for (var i = 0; i < FFMPEG_CANDIDATES.length; i++) {
      try { if (fs.existsSync(FFMPEG_CANDIDATES[i])) return FFMPEG_CANDIDATES[i]; } catch (e) {}
    }
    // fallback: 'ffmpeg' no PATH
    return "ffmpeg";
  }

  function tmpFile(ext) {
    return path.join(os.tmpdir(), "viralcut_" + Date.now() + "_" + Math.floor(Math.random() * 1e6) + ext);
  }

  // ---------------------------------------------------------------------------
  // Extracao de audio (ffmpeg -> mp3 16kHz mono, leve p/ Whisper)
  // ---------------------------------------------------------------------------
  function extractAudio(videoPath) {
    return new Promise(function (resolve, reject) {
      var out = tmpFile(".mp3");
      var ff = findFfmpeg();
      childProcess.execFile(
        ff,
        ["-y", "-i", videoPath, "-vn", "-ar", "16000", "-ac", "1", "-b:a", "64k", out],
        { timeout: 600000 },
        function (err, stdout, stderr) {
          if (err) return reject(new Error("ffmpeg falhou: " + (stderr || err.message).slice(-400)));
          if (!fs.existsSync(out)) return reject(new Error("ffmpeg nao gerou audio"));
          resolve(out);
        }
      );
    });
  }

  // ---------------------------------------------------------------------------
  // Transcricao (Whisper, word-level)
  // ---------------------------------------------------------------------------
  function contentTypeFor(p) {
    var e = p.toLowerCase();
    if (e.indexOf(".mp3") >= 0) return "audio/mpeg";
    if (e.indexOf(".wav") >= 0) return "audio/wav";
    if (e.indexOf(".m4a") >= 0) return "audio/mp4";
    return "application/octet-stream";
  }

  async function whisperTranscribe(mp3Path, apiKey) {
    var st = fs.statSync(mp3Path);
    if (st.size > 24 * 1024 * 1024) {
      throw new Error("Vídeo longo demais para transcrição direta (~50min+). Divida em partes menores.");
    }
    var buffer = fs.readFileSync(mp3Path);
    var u8 = new Uint8Array(buffer.byteLength);
    u8.set(buffer);
    var blob = new Blob([u8], { type: contentTypeFor(mp3Path) });

    var form = new FormData();
    form.append("file", blob, path.basename(mp3Path));
    form.append("model", "whisper-1");
    form.append("response_format", "verbose_json");
    form.append("language", "pt");
    form.append("timestamp_granularities[]", "segment");
    form.append("timestamp_granularities[]", "word");

    var res = await fetch(WHISPER_URL, {
      method: "POST",
      headers: { Authorization: "Bearer " + apiKey },
      body: form
    });
    if (!res.ok) {
      var detail = "HTTP " + res.status;
      try { var j = await res.json(); if (j.error && j.error.message) detail = j.error.message; } catch (e) {}
      throw new Error("Whisper falhou: " + detail);
    }
    var json = await res.json();
    return buildTranscript(json);
  }

  // Monta words[] + segments[] com word_ids (a fonte de verdade de tempo).
  function buildTranscript(json) {
    var rawWords = Array.isArray(json.words) ? json.words : [];
    var words = [];
    for (var i = 0; i < rawWords.length; i++) {
      var w = rawWords[i];
      var start = typeof w.start === "number" ? w.start : 0;
      var end = typeof w.end === "number" ? w.end : 0;
      if (end <= start) continue;
      words.push({ id: words.length, text: String(w.word || "").trim(), start: start, end: end });
    }
    var rawSegs = Array.isArray(json.segments) ? json.segments : [];
    var segments = [];
    for (var s = 0; s < rawSegs.length; s++) {
      var seg = rawSegs[s];
      var ss = typeof seg.start === "number" ? seg.start : 0;
      var se = typeof seg.end === "number" ? seg.end : 0;
      if (se <= ss) continue;
      var wids = [];
      for (var k = 0; k < words.length; k++) {
        if (words[k].start >= ss - 0.001 && words[k].end <= se + 0.001) wids.push(words[k].id);
      }
      segments.push({ id: segments.length, start: ss, end: se, text: (seg.text || "").trim(), word_ids: wids });
    }
    return { words: words, segments: segments };
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

  function resolveClips(raw, transcript) {
    var out = [];
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
    return out;
  }

  // ---------------------------------------------------------------------------
  // Plano de cortes p/ ExtendScript (ticks, snapados no frame)
  // ---------------------------------------------------------------------------
  function secToTicks(sec, fps) {
    var frame = Math.round(sec * fps);
    return String(Math.round(frame * TICKS_PER_SEC / fps));
  }

  function buildCutPlan(clips, source, seqName) {
    var cuts = [];
    for (var i = 0; i < clips.length; i++) {
      var c = clips[i];
      cuts.push({
        id: c.id,
        titulo: c.titulo,
        project_item_id: source.project_item_id,
        in_ticks: secToTicks(c.start, source.fps),
        out_ticks: secToTicks(c.end, source.fps),
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
## DURAÇÃO: ~15 a ~90s. Coerência do arco acima de duração.
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

  function buildMontagePlan(montage, source, seqName) {
    var cuts = [];
    for (var i = 0; i < montage.pieces.length; i++) {
      var p = montage.pieces[i];
      cuts.push({
        id: montage.id + "_" + i, titulo: montage.titulo,
        project_item_id: source.project_item_id,
        in_ticks: secToTicks(p.start, source.fps),
        out_ticks: secToTicks(p.end, source.fps),
        label_index: 8
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

  // ===========================================================================
  // ORQUESTRACAO PUBLICA
  // ===========================================================================
  async function transcribe(source, onProgress) {
    onProgress = onProgress || function () {};
    logReset(source);
    var apiKey = readOpenAIKey();

    onProgress(20, "Extraindo áudio…");
    var mp3 = await extractAudio(source.path);

    onProgress(55, "Transcrevendo (Whisper)…");
    var transcript = await whisperTranscribe(mp3, apiKey);
    try { fs.unlinkSync(mp3); } catch (e) {}
    if (!transcript.segments.length) throw new Error("Nenhuma fala detectada no vídeo.");

    logSet("whisper", {
      words: transcript.words.length,
      segments: transcript.segments.length,
      full_text: transcript.segments.map(function (s) { return s.text; }).join(" ")
    });
    onProgress(100, transcript.words.length + " palavras transcritas");
    return transcript;
  }

  async function viralCuts(transcript, source, opts) {
    opts = opts || {};
    var apiKey = readOpenAIKey();
    var sink = { type: "viral" };
    var user = buildUserPrompt(transcript, opts.minScore || 45, opts.maxClips || 12, opts.minDur || 15, opts.maxDur || 90);
    var out = await gptCall(apiKey, SYSTEM_PROMPT_VIRAL, user, "propose_clips", CLIP_SCHEMA, sink);
    var clips = resolveClips(out.clips || [], transcript);
    sink.resolved = clips.map(function (c) { return { titulo: c.titulo, score: c.score, start: c.start, end: c.end, text: c.text }; });
    sink.summary = clips.length + " cortes virais";
    logObjective(sink);
    return { clips: clips };
  }

  async function frankenbite(transcript, source, opts) {
    opts = opts || {};
    var apiKey = readOpenAIKey();
    var sink = { type: "frankenbite" };
    var segLines = [];
    for (var si = 0; si < transcript.segments.length; si++) {
      var s = transcript.segments[si];
      segLines.push("[seg " + s.id + " | " + s.start.toFixed(1) + "-" + s.end.toFixed(1) + "] " + s.text);
    }
    var user = "MÁX MONTAGENS: " + (opts.maxClips || 3) + ". DURAÇÃO ALVO: " + (opts.minDur || 15) +
      "-" + (opts.maxDur || 90) + "s.\n\nTRANSCRIÇÃO (id | tempo | texto):\n" + segLines.join("\n") +
      "\n\nCrie montagens costurando segmentos de momentos diferentes numa narrativa nova e mais forte. " +
      "Cada montagem é uma lista ORDENADA de ids (ordem de reprodução). Ordene as montagens por score.";
    var out = await gptCall(apiKey, SYSTEM_PROMPT_MONTAGE, user, "propose_montages", MONTAGE_SCHEMA, sink);
    var raw = out.montagens || [];
    var montages = [];
    for (var i = 0; i < raw.length; i++) { var m = resolveMontage(raw[i], transcript, i); if (m) montages.push(m); }
    montages.sort(function (a, b) { return b.score - a.score; });
    sink.resolved = montages.map(function (m) { return { titulo: m.titulo, score: m.score, pieces: m.pieces.length, text: m.text }; });
    sink.summary = montages.length + " montagens";
    logObjective(sink);
    return { montages: montages };
  }

  function removeSilences(transcript, source, opts) {
    opts = opts || {};
    var gap = opts.gap || 0.6;
    var spans = detectSpokenSpans(transcript, gap);
    var cuts = [];
    var kept = 0;
    for (var i = 0; i < spans.length; i++) {
      var s = spans[i];
      var st = Math.max(0, s.start - 0.03), en = s.end + 0.03;
      kept += (en - st);
      cuts.push({
        id: "sil_" + i, titulo: "Fala " + (i + 1),
        project_item_id: source.project_item_id,
        in_ticks: secToTicks(st, source.fps),
        out_ticks: secToTicks(en, source.fps),
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
    _buildTranscript: buildTranscript,
    _resolveClips: resolveClips,
    _secToTicks: secToTicks,
    _detectSpokenSpans: detectSpokenSpans,
    _readOpenAIKey: readOpenAIKey
  };

  if (typeof window !== "undefined") window.VIRALCUT_CORE = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
