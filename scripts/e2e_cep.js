// Teste e2e dos 3 objetivos (mesmo código do painel). Gera o log em logs/last-run.json.
// Uso: node scripts/e2e_cep.js <audio-ou-video>
const core = require("../premiere-panel/client/core-cep.js");

(async () => {
  // seq como getTimelineClips retornaria: 1 video cobrindo toda a midia.
  // (O video de teste vira 1 clipe; o remap/split e exercitado pelos testes unitarios.)
  const seq = {
    name: "TESTE", fps: 30, duration_sec: 66,
    clips: [{ source_key: process.argv[2], ref: "TEST_NODE", src_in: 0, tl_start: 0, tl_end: 66 }],
  };

  console.log("== TRANSCREVER ==");
  const transcript = await core.transcribe(seq, (p, m) => console.log(`  [${p}%] ${m}`));

  console.log("\n== OBJETIVO 1: FALAS VIRAIS ==");
  const { clips } = await core.viralCuts(transcript, seq, {});
  clips.forEach(c => console.log(`  [${c.score}] ${c.titulo} (${c.start.toFixed(1)}-${c.end.toFixed(1)}s)`));

  console.log("\n== OBJETIVO 2: MONTAR FALAS (frankenbite) ==");
  const { montages } = await core.frankenbite(transcript, seq, {});
  montages.forEach(m => {
    console.log(`  [${m.score}] ${m.titulo} — ${m.pieces.length} trechos`);
    console.log(`       ${m.text}`);
  });

  console.log("\n== OBJETIVO 3: REMOVER SILÊNCIOS ==");
  const sil = core.removeSilences(transcript, seq, {});
  console.log(`  ${sil.summary.spans} falas, original ${Math.round(sil.summary.original_sec)}s -> ${Math.round(sil.summary.new_sec)}s (economia ${Math.round(sil.summary.saved_sec)}s)`);

  console.log("\n== PLANOS (o que iria pra timeline) ==");
  const p1 = core.buildCutPlan(clips, seq, "Cortes Virais — TESTE");
  console.log(`  Falas virais: ${p1.cuts.length} clips coloridos numa nova sequência`);
  if (montages[0]) {
    const p2 = core.buildMontagePlan(montages[0], seq, "Montagem 1");
    console.log(`  Montagem 1: ${p2.cuts.length} trechos encaixados numa nova sequência`);
  }
  console.log(`  Sem silêncios: ${sil.plan.cuts.length} trechos falados contíguos`);

  console.log("\nLog salvo em logs/last-run.json");
})().catch(e => { console.error("ERRO:", e.message); process.exit(1); });
