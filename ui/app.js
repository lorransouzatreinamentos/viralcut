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
      info = await api("/timeline/select", { method: "POST" });
    }
    $("tlInfo").innerHTML = `<b>${info.name}</b> · ${Math.round(info.duration_sec)}s · ${info.fps?.toFixed?.(2) || "?"} fps`;
    $("btnAnalyze").disabled = false;
  } catch (e) { msg($("tlInfo"), e.message, "err"); }
};

// --- Passo 2: transcrever (uma vez) ---
$("btnAnalyze").onclick = async () => {
  $("btnAnalyze").disabled = true;
  $("progressWrap").style.display = "block";
  try {
    if (!IS_PREMIERE) throw new Error("Disponível no Premiere. (DaVinci em breve.)");
    transcript = await core().transcribe(window.__source, (p, t) => setP(p, t));
    $("objStep").style.display = "block";
    $("analyzeDone").textContent = `✓ ${transcript.words.length} palavras transcritas — escolha um objetivo (pode repetir).`;
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

$("btnObjViral").onclick = async () => runObjective("viral");
$("btnObjFrank").onclick = async () => runObjective("frankenbite");
$("btnObjSilence").onclick = async () => runObjective("silence");

async function runObjective(name) {
  if (!transcript) return;
  selectObj(name);
  $("results").innerHTML = `<div class="dim">Processando…</div>`;
  $("btnApply").style.display = "none";
  $("applyMsg").innerHTML = "";
  try {
    if (name === "viral") {
      currentData = await core().viralCuts(transcript, window.__source, {});
      renderClips(currentData.clips);
    } else if (name === "frankenbite") {
      currentData = await core().frankenbite(transcript, window.__source, {});
      renderMontages(currentData.montages);
    } else {
      currentData = core().removeSilences(transcript, window.__source, {});
      renderSilence(currentData.summary);
    }
    $("btnApply").style.display = "block";
  } catch (e) { msg($("results"), e.message, "err"); }
}

function renderClips(clips) {
  if (!clips.length) return msg($("results"), "Nenhum corte encontrado.", "err");
  $("results").innerHTML = clips.map((c) => `
    <label class="card"><input type="checkbox" data-id="${c.id}" checked>
      <div><div class="title">${c.titulo}</div>
        <div class="meta">${fmt(c.start)}–${fmt(c.end)} · ${Math.round(c.end - c.start)}s</div>
        <div class="hook">"${c.hook_first_3s || c.motivo || ""}"</div></div>
      <span class="score">${c.score}</span></label>`).join("");
  $("btnApply").textContent = "Aplicar cortes selecionados";
}

function renderMontages(montages) {
  if (!montages.length) return msg($("results"), "Nenhuma montagem gerada.", "err");
  // Cada montagem marcada = 1 nova timeline. Por padrão marca só a melhor,
  // para não criar várias timelines sem querer.
  $("results").innerHTML =
    `<div class="dim" style="margin-bottom:6px;">Cada montagem marcada vira uma nova timeline. Marque as que quiser.</div>` +
    montages.map((m, i) => `
    <label class="card"><input type="checkbox" data-id="${m.id}" ${i === 0 ? "checked" : ""}>
      <div><div class="title">${m.titulo}</div>
        <div class="meta">${m.pieces.length} trechos costurados</div>
        <div class="hook">${m.text}</div></div>
      <span class="score">${m.score}</span></label>`).join("");
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
    if (!IS_PREMIERE) throw new Error("Disponível no Premiere.");
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
    if (IS_PREMIERE) await core().updatePanel(); else await api("/panel/update", { method: "POST" });
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
  }
  $("ver").textContent = ver;
})();
