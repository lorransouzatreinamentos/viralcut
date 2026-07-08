/* VIRALCUT — UI. Fluxo: 1) selecionar sequência, 2) transcrever (1x),
 * 3) escolher objetivo (reaplicável): falas virais / montar falas / remover silêncios.
 * No Premiere tudo roda local via window.VIRALCUT_CORE (Node no CEP). */

const IS_PREMIERE = typeof window.__adobe_cep__ !== "undefined";
const HOST = IS_PREMIERE ? "premiere" : "davinci";
const CORE = "http://127.0.0.1:8756"; // fluxo DaVinci (futuro)

let transcript = null;      // transcrição em cache (transcreve 1x)
let currentObj = null;      // "viral" | "frankenbite" | "silence"
let currentData = null;     // resultado do objetivo atual

const $ = (id) => document.getElementById(id);
const setP = (pct, txt) => { $("progressBar").style.width = pct + "%"; $("progressTxt").textContent = txt; };
const msg = (el, text, kind) => { el.innerHTML = `<div class="msg ${kind}">${text}</div>`; };
const fmt = (sec) => { const m = Math.floor(sec / 60), s = Math.round(sec % 60); return `${m}:${String(s).padStart(2, "0")}`; };

const api = async (path, opts = {}) => {
  const res = await fetch(CORE + path, { method: opts.method || "GET", headers: { "Content-Type": "application/json" }, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
};

function evalES(fnCall) {
  return new Promise((resolve, reject) => {
    if (!IS_PREMIERE || !window.__csi) return reject(new Error("bridge CEP indisponível"));
    window.__csi.evalScript(fnCall, (r) => { try { resolve(JSON.parse(r)); } catch { reject(new Error("resposta ExtendScript inválida: " + r)); } });
  });
}
function core() { if (!window.VIRALCUT_CORE) throw new Error("Núcleo não carregou. Reinicie o Premiere."); return window.VIRALCUT_CORE; }

// --- Passo 1: selecionar sequência ---
$("btnSelect").onclick = async () => {
  try {
    let info;
    if (IS_PREMIERE) {
      info = await evalES("VIRALCUT.getSourceMediaPath()");
      if (info.error) throw new Error(info.error);
      window.__source = info;
    } else {
      info = await api("/davinci/select", { method: "POST" });
      window.__source = info;
    }
    // Mostra a TIMELINE e o VÍDEO separados. Antes exibia só o nome do clipe
    // num botão chamado "selecionar sequência", o que fazia parecer que o
    // usuário tinha selecionado um vídeo em vez da timeline.
    const tl = info.timeline_name
      ? `<div>Timeline: <b>${info.timeline_name}</b></div>`
      : "";
    $("tlInfo").innerHTML = tl +
      `<div>Vídeo: <b>${info.name}</b> · ${Math.round(info.duration_sec)}s · ${info.fps?.toFixed?.(2) || "?"} fps</div>`;
    $("btnAnalyze").disabled = false;
  } catch (e) { msg($("tlInfo"), e.message, "err"); }
};

// --- Passo 2: transcrever (uma vez) ---
$("btnAnalyze").onclick = async () => {
  $("btnAnalyze").disabled = true;
  $("progressWrap").style.display = "block";
  try {
    let words;
    if (IS_PREMIERE) {
      transcript = await core().transcribe(window.__source, (p, t) => setP(p, t));
      words = transcript.words.length;
    } else {
      setP(30, "Transcrevendo (pode levar 1-2 min)…");
      const r = await api("/davinci/transcribe", { method: "POST" });
      transcript = true; // no DaVinci a transcrição fica no Core; a UI só precisa saber que existe
      words = r.words;
      setP(100, `${words} palavras`);
    }
    $("objStep").style.display = "block";
    $("analyzeDone").textContent = `✓ ${words} palavras transcritas — escolha um objetivo (pode repetir).`;
  } catch (e) {
    setP(0, "");
    msg($("progressTxt"), e.message, "err");
    $("btnAnalyze").disabled = false;
  }
};

// --- Passo 3: objetivos ---
function selectObj(name) {
  currentObj = name;
  ["Viral", "Frank", "Silence"].forEach((k) => $("btnObj" + k).classList.remove("active"));
  $("btnObj" + ({ viral: "Viral", frankenbite: "Frank", silence: "Silence" })[name]).classList.add("active");
}

// Escolher o objetivo mostra suas OPÇÕES; o usuário ajusta e então executa.
$("btnObjViral").onclick = () => showOpts("viral");
$("btnObjFrank").onclick = () => showOpts("frankenbite");
$("btnObjSilence").onclick = () => showOpts("silence");

const num = (id, fallback) => {
  const el = $(id);
  const v = el ? parseFloat(el.value) : NaN;
  return isNaN(v) ? fallback : v;
};

function showOpts(name) {
  if (!transcript) return;
  selectObj(name);
  $("results").innerHTML = "";
  $("btnApply").style.display = "none";
  $("applyMsg").innerHTML = "";

  const box = $("opts");
  box.style.display = "block";

  if (name === "viral") {
    box.innerHTML = `
      <div class="opt"><label>Duração mínima do corte</label><input id="optMinDur" type="number" min="1" step="5" value="30"> </div>
      <div class="opt"><label>Duração máxima do corte</label><input id="optMaxDur" type="number" min="2" step="5" value="90"> </div>
      <div class="dim" style="margin:6px 0 8px;">Cortes fora dessa faixa são descartados — evita trechos curtos sem sentido.</div>
      <button id="btnRun">Extrair falas virais</button>`;
  } else if (name === "frankenbite") {
    box.innerHTML = `
      <div class="opt"><label>Quantos vídeos montar</label><input id="optNVideos" type="number" min="1" max="8" step="1" value="3"> </div>
      <div class="opt"><label>Duração mínima de cada</label><input id="optMinDur" type="number" min="1" step="5" value="30"> </div>
      <div class="opt"><label>Duração máxima de cada</label><input id="optMaxDur" type="number" min="2" step="5" value="90"> </div>
      <div class="dim" style="margin:6px 0 8px;">Cada vídeo montado vira uma timeline com sua própria cor.</div>
      <button id="btnRun">Montar falas</button>`;
  } else {
    box.innerHTML = `
      <div class="opt"><label>Cortar pausas a partir de (s)</label><input id="optGap" type="number" min="0.2" step="0.1" value="0.6"> </div>
      <div class="dim" style="margin:6px 0 8px;">Pausas maiores que isso são removidas. As falas ganham uma folga automática para não cortar a última palavra.</div>
      <button id="btnRun">Analisar silêncios</button>`;
  }
  $("btnRun").onclick = () => runObjective(name);
}

function readOpts(name) {
  if (name === "viral") return { minDur: num("optMinDur", 30), maxDur: num("optMaxDur", 90) };
  if (name === "frankenbite") return { nVideos: num("optNVideos", 3), minDur: num("optMinDur", 30), maxDur: num("optMaxDur", 90) };
  return { gap: num("optGap", 0.6) };
}

async function runObjective(name) {
  if (!transcript) return;
  const o = readOpts(name);
  if ((name === "viral" || name === "frankenbite") && o.minDur >= o.maxDur) {
    return msg($("results"), "A duração mínima precisa ser menor que a máxima.", "err");
  }
  $("results").innerHTML = `<div class="dim">Processando…</div>`;
  $("btnApply").style.display = "none";
  $("applyMsg").innerHTML = "";
  try {
    if (name === "viral") {
      currentData = IS_PREMIERE
        ? await core().viralCuts(transcript, window.__source, o)
        : await api("/davinci/viral", { method: "POST", body: { min_dur: o.minDur, max_dur: o.maxDur } });
      renderClips(currentData.clips, currentData.rejected, o);
    } else if (name === "frankenbite") {
      currentData = IS_PREMIERE
        ? await core().frankenbite(transcript, window.__source, o)
        : await api("/davinci/frankenbite", { method: "POST", body: { n_videos: o.nVideos, min_dur: o.minDur, max_dur: o.maxDur } });
      renderMontages(currentData.montages, o);
    } else {
      currentData = IS_PREMIERE
        ? core().removeSilences(transcript, window.__source, o)
        : { summary: await api("/davinci/silences", { method: "POST" }) };
      renderSilence(currentData.summary);
    }
    $("btnApply").style.display = "block";
  } catch (e) { msg($("results"), e.message, "err"); }
}

// Cores de preview (mesma ordem do COLOR_CYCLE usado na timeline)
const SWATCHES = ["#4f8cff", "#a86ff0", "#f08a3c", "#3fb950", "#f06fa8", "#3cc9d6", "#e6c84a", "#3f5ab9"];

function rejectedNote(rejected, o) {
  if (!rejected) return "";
  const n = (rejected.curto || 0) + (rejected.longo || 0);
  if (!n) return "";
  const partes = [];
  if (rejected.curto) partes.push(`${rejected.curto} curto(s) demais`);
  if (rejected.longo) partes.push(`${rejected.longo} longo(s) demais`);
  return `<div class="dim" style="margin-bottom:6px;">Descartados fora da faixa ${o.minDur}–${o.maxDur}s: ${partes.join(", ")}.</div>`;
}

function renderClips(clips, rejected, o) {
  if (!clips.length) {
    const nota = rejected && (rejected.curto || rejected.longo)
      ? ` A IA propôs cortes, mas todos ficaram fora da faixa ${o.minDur}–${o.maxDur}s. Tente ampliar a faixa.`
      : "";
    return msg($("results"), "Nenhum corte encontrado." + nota, "err");
  }
  $("results").innerHTML = rejectedNote(rejected, o) + clips.map((c) => `
    <label class="card"><input type="checkbox" data-id="${c.id}" checked>
      <div><div class="title">${c.titulo}</div>
        <div class="meta">${fmt(c.start)}–${fmt(c.end)} · ${Math.round(c.end - c.start)}s</div>
        <div class="hook">"${c.hook_first_3s || c.motivo || ""}"</div></div>
      <span class="score">${c.score}</span></label>`).join("");
  $("btnApply").textContent = "Aplicar cortes selecionados";
}

function renderMontages(montages, o) {
  if (!montages.length) return msg($("results"), "Nenhuma montagem gerada. Tente ampliar a faixa de duração.", "err");
  const pedido = o && o.nVideos;
  const aviso = pedido && montages.length < pedido
    ? `<div class="dim" style="margin-bottom:6px;">Você pediu ${pedido}, mas o vídeo só rendeu ${montages.length} montagem(ns) com material bom.</div>`
    : "";
  // Cada montagem marcada = 1 nova timeline, cada uma com sua cor.
  $("results").innerHTML = aviso +
    `<div class="dim" style="margin-bottom:6px;">Cada montagem marcada vira uma timeline com sua própria cor.</div>` +
    montages.map((m, i) => {
      const cor = SWATCHES[i % SWATCHES.length];
      const dur = m.pieces.reduce((a, p) => a + (p.end - p.start), 0);
      return `
    <label class="card"><input type="checkbox" data-id="${m.id}" ${i === 0 ? "checked" : ""}>
      <div><div class="title"><span class="swatch" style="background:${cor}"></span>${m.titulo}</div>
        <div class="meta">${m.pieces.length} trechos · ~${Math.round(dur)}s</div>
        <div class="hook">${m.text}</div></div>
      <span class="score">${m.score}</span></label>`;
    }).join("");
  $("btnApply").textContent = "Criar montagens selecionadas";
}

function renderSilence(s) {
  $("results").innerHTML = `<div class="card"><div>
    <div class="title">Remover silêncios</div>
    <div class="meta">${s.spans} falas detectadas · corte em pausas ≥ ${s.gap_threshold}s</div>
    <div class="hook">Duração: ${Math.round(s.original_sec)}s → ${Math.round(s.new_sec)}s (economiza ${Math.round(s.saved_sec)}s)</div>
  </div></div>`;
  $("btnApply").textContent = "Aplicar (nova sequência sem silêncios)";
}

// --- Aplicar (por objetivo) ---
$("btnApply").onclick = async () => {
  $("btnApply").disabled = true;
  try {
    // --- DaVinci: o Core Python aplica direto no Resolve ---
    if (!IS_PREMIERE) {
      const objMap = { viral: "viral", frankenbite: "frankenbite", silence: "silences" };
      let ids = null;
      if (currentObj === "viral" || currentObj === "frankenbite") {
        ids = [...document.querySelectorAll('#results input:checked')].map((cb) => cb.dataset.id);
        if (!ids.length) throw new Error("Selecione ao menos um item.");
      }
      $("applyMsg").innerHTML = `<div class="dim">Aplicando no Resolve…</div>`;
      const r = await api("/davinci/apply", { method: "POST", body: { objective: objMap[currentObj], ids } });
      if (r.sequences !== undefined) msg($("applyMsg"), `✓ ${r.sequences} montagem(ns) criada(s).`, "ok");
      else msg($("applyMsg"), `✓ Timeline "${r.new_timeline_name}" criada (${r.applied} trechos).`, "ok");
      return;
    }
    const src = window.__source;
    const seqBase = src.name || "sequencia";
    let result;
    if (currentObj === "viral") {
      const ids = [...document.querySelectorAll('#results input:checked')].map((cb) => cb.dataset.id);
      const sel = currentData.clips.filter((c) => ids.indexOf(c.id) >= 0);
      if (!sel.length) throw new Error("Selecione ao menos um corte.");
      const plan = core().buildCutPlan(sel, src, "Cortes Virais — " + seqBase);
      result = await evalES(`VIRALCUT.applyCutPlan(${JSON.stringify(JSON.stringify(plan))})`);
      if (result.error) throw new Error(result.error);
      core().logApplied("viral", result);
      msg($("applyMsg"), `✓ Sequência "${result.new_sequence_name}" com ${result.created} cortes.`, "ok");
    } else if (currentObj === "frankenbite") {
      const ids = [...document.querySelectorAll('#results input:checked')].map((cb) => cb.dataset.id);
      const sel = currentData.montages.filter((m) => ids.indexOf(m.id) >= 0);
      if (!sel.length) throw new Error("Selecione ao menos uma montagem.");
      let created = 0;
      for (let i = 0; i < sel.length; i++) {
        const plan = core().buildMontagePlan(sel[i], src, `Montagem ${i + 1} — ${seqBase}`);
        const r = await evalES(`VIRALCUT.applyCutPlan(${JSON.stringify(JSON.stringify(plan))})`);
        if (!r.error) created++;
      }
      core().logApplied("frankenbite", { sequences: created });
      msg($("applyMsg"), `✓ ${created} montagem(ns) criada(s) como novas sequências.`, "ok");
    } else {
      const r = await evalES(`VIRALCUT.applyCutPlan(${JSON.stringify(JSON.stringify(currentData.plan))})`);
      if (r.error) throw new Error(r.error);
      core().logApplied("silence", r);
      msg($("applyMsg"), `✓ Sequência "${r.new_sequence_name}" sem silêncios (${r.created} trechos).`, "ok");
    }
  } catch (e) { msg($("applyMsg"), e.message, "err"); }
  finally { $("btnApply").disabled = false; }
};

// --- Atualização do app (link no título) ---
$("title").onclick = async () => {
  const prev = $("host").textContent;
  $("host").textContent = "atualizando…";
  try {
    if (IS_PREMIERE) await core().updatePanel(); else await api("/update", { method: "POST" });
    location.reload();
  } catch (e) { $("host").textContent = prev; msg($("tlInfo"), "Falha ao atualizar: " + e.message, "err"); }
};

// --- Init ---
async function hostVersionWithRetry(tries) {
  for (let i = 0; i < tries; i++) {
    try { const v = await evalES("VIRALCUT.version()"); if (v && v.build) return v.build; } catch (e) {}
    await new Promise((r) => setTimeout(r, 350));
  }
  return null;
}
(async function init() {
  $("host").textContent = HOST === "premiere" ? "Premiere Pro" : "DaVinci Resolve";
  let ver = window.__VIRALCUT_VERSION || "dev";
  if (IS_PREMIERE) {
    if (!window.VIRALCUT_CORE) msg($("tlInfo"), "Núcleo (Node) não carregou. Reinicie o Premiere (Cmd+Q e reabra).", "err");
    const build = await hostVersionWithRetry(4);
    if (build) ver += " · host " + build;
    else msg($("tlInfo"), "ExtendScript não carregou. Feche e reabra o painel (Window > Extensions). Se persistir, reinicie o Premiere.", "err");
  } else {
    try { ver = (await api("/version")).version || "dev"; } catch (e) {}
  }
  $("ver").textContent = ver;
})();
