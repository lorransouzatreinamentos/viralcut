/**
 * Trava o espelho JS de core/timeline_map.py (remap + split) no painel Premiere.
 * O painel e o Core Python TEM que produzir o mesmo resultado numa timeline
 * multi-video, senao Premiere e DaVinci divergem. Roda com: node scripts/test_timeline_map_js.js
 */
const assert = require("assert");
const c = require("../premiere-panel/client/core-cep.js");

function t(words) {
  const ws = words.map(([id, text, start, end]) => ({ id, text, start, end }));
  const segs = ws.map((w) => ({ id: w.id, start: w.start, end: w.end, text: w.text, word_ids: [w.id] }));
  return { words: ws, segments: segs };
}

// --- remap: os 5 videos aparecem, em ordem de timeline ---
(function () {
  const clips = [], transcripts = {};
  for (let i = 0; i < 5; i++) {
    const key = "v" + i;
    transcripts[key] = t([[0, "fala" + i, 0.0, 0.5]]);
    clips.push({ source_key: key, ref: key, src_in: 0.0, tl_start: i, tl_end: i + 0.5 });
  }
  const out = c._remapToTimeline(clips, transcripts);
  assert.deepStrictEqual(out.words.map((w) => w.text), ["fala0", "fala1", "fala2", "fala3", "fala4"], "5 videos");
  // ids contiguos
  assert.deepStrictEqual(out.words.map((w) => w.id), [0, 1, 2, 3, 4], "ids remapeados");
})();

// --- remap: ordem da timeline manda, nao a da lista ---
(function () {
  const clips = [
    { source_key: "b", ref: "b", src_in: 0, tl_start: 5, tl_end: 5.5 },
    { source_key: "a", ref: "a", src_in: 0, tl_start: 0, tl_end: 0.5 },
  ];
  const out = c._remapToTimeline(clips, { a: t([[0, "primeiro", 0, 0.5]]), b: t([[0, "segundo", 0, 0.5]]) });
  assert.deepStrictEqual(out.words.map((w) => w.text), ["primeiro", "segundo"], "ordem timeline");
})();

// --- remap: apara fala fora do trecho usado ---
(function () {
  const vid = t([[0, "antes", 0, 0.5], [1, "usado", 2.0, 2.5], [2, "depois", 5.0, 5.5]]);
  const clips = [{ source_key: "v", ref: "v", src_in: 1.8, tl_start: 0, tl_end: 1.0 }]; // 1.8-2.8
  const out = c._remapToTimeline(clips, { v: vid });
  assert.deepStrictEqual(out.words.map((w) => w.text), ["usado"], "trim in/out");
})();

// --- split: corte dentro de 1 clipe -> 1 span ---
(function () {
  const clips = [
    { source_key: "a", ref: "a", src_in: 10, tl_start: 0, tl_end: 4 },
    { source_key: "b", ref: "b", src_in: 0, tl_start: 4, tl_end: 8 },
  ];
  const one = c._splitCut(1.0, 3.0, clips);
  assert.strictEqual(one.length, 1);
  assert.strictEqual(one[0].ref, "a");
  assert.strictEqual(one[0].src_start, 11.0);

  // corte cruzando -> 2 spans
  const two = c._splitCut(2.0, 6.0, clips);
  assert.strictEqual(two.length, 2, "cruzar fronteira = 2 spans");
  assert.strictEqual(two[0].ref, "a"); assert.strictEqual(two[0].src_end, 14.0);
  assert.strictEqual(two[1].ref, "b"); assert.strictEqual(two[1].src_start, 0.0);

  // gap entre clipes -> nenhum span
  const gapClips = [
    { source_key: "a", ref: "a", src_in: 0, tl_start: 0, tl_end: 2 },
    { source_key: "b", ref: "b", src_in: 0, tl_start: 5, tl_end: 7 },
  ];
  assert.deepStrictEqual(c._splitCut(2.5, 4.5, gapClips), [], "corte no gap");
})();

// --- buildCutPlan: corte que cruza -> 2 cuts, mesma cor, itens diferentes ---
(function () {
  const seq = { fps: 30, clips: [
    { source_key: "a", ref: "A", src_in: 10, tl_start: 0, tl_end: 4 },
    { source_key: "b", ref: "B", src_in: 0, tl_start: 4, tl_end: 8 },
  ] };
  const plan = c._buildCutPlan([{ id: "v0", titulo: "x", start: 2.0, end: 6.0, color_index: 9 }], seq, "S");
  assert.strictEqual(plan.cuts.length, 2);
  assert.deepStrictEqual(plan.cuts.map((x) => x.label_index), [9, 9], "mesma cor nos 2 pedacos");
  assert.deepStrictEqual(plan.cuts.map((x) => x.project_item_id), ["A", "B"], "itens diferentes");
})();

// --- montagem: espalhamento (espelho de core/objectives.py) ---
(function () {
  // 20 segmentos de 5s cada (video de 100s)
  const segs = [];
  for (let i = 0; i < 20; i++) segs.push({ id: i, start: i * 5, end: i * 5 + 4.9, text: "f" + i, word_ids: [i] });
  const t = { words: segs.map((s) => ({ id: s.id, text: s.text, start: s.start, end: s.end })), segments: segs };

  assert.strictEqual(c._montageIsSpread([5, 6, 7, 8], t), false, "vizinhos = corte linear, rejeita");
  assert.strictEqual(c._montageIsSpread([2, 9, 17], t), true, "comeco/meio/fim = espalhada, ok");
  assert.strictEqual(c._montageIsSpread([1, 18], t), false, "so 2 pecas nao e montagem");
  // buraco da adjacencia (espelho do Python): span alto mas 0,1,2 colados -> rejeita
  assert.strictEqual(c._montageIsSpread([0, 1, 2, 18], t), false, "span alto + segmentos colados = rejeita");
  assert.strictEqual(c._montageIsSpread([1, 6, 7, 14, 19], t), true, "1 par vizinho entre saltos = ok");
  // dedup de montagens quase iguais
  assert.strictEqual(c._segmentsTooSimilar([1, 2, 3, 4], [1, 2, 3, 9]), true, "3/4 = clone");
  assert.strictEqual(c._segmentsTooSimilar([1, 2, 3, 4], [1, 5, 6, 7]), false, "1/4 = diferente");
})();

// --- viral: resolveClips filtra por score e corta em maxClips (espelho do Python) ---
(function () {
  const segs = [];
  for (let i = 0; i < 6; i++) segs.push({ id: i, start: i * 40, end: i * 40 + 30, text: "s" + i, word_ids: [i] });
  const words = segs.map((s) => ({ id: s.id, text: s.text, start: s.start, end: s.end }));
  const t = { words: words, segments: segs };
  const raw = [
    { start_seg_id: 0, end_seg_id: 0, titulo: "bom", score: 80 },
    { start_seg_id: 1, end_seg_id: 1, titulo: "fraco", score: 20 },   // abaixo do minScore
    { start_seg_id: 2, end_seg_id: 2, titulo: "ok", score: 60 },
    { start_seg_id: 3, end_seg_id: 3, titulo: "medio", score: 55 },
  ];
  const out = c._resolveClips(raw, t, 5, 60, 45, 2);  // minScore 45, maxClips 2
  assert.strictEqual(out.length, 2, "deveria cortar em maxClips=2");
  assert.ok(out.every((x) => x.score >= 45), "nao deveria manter score < minScore");
  assert.strictEqual(out[0].titulo, "bom", "ordenado por score desc");
})();

// --- viral: faixa e grace band (fala completa perto da borda sobrevive) ---
(function () {
  // transcript: seg de ~20s
  const words = [{ id: 0, text: "a", start: 10, end: 12 }, { id: 1, text: "b", start: 12, end: 30 }];
  const segments = [{ id: 0, start: 10, end: 30, text: "fala 20s", word_ids: [0, 1] }];
  const t = { words: words, segments: segments };
  // min 30, max 40: grace floor = 15, teto = 60 -> 20s sobrevive
  const kept = c._resolveClips([{ start_seg_id: 0, end_seg_id: 0, titulo: "x", score: 80 }], t, 30, 40);
  assert.strictEqual(kept.length, 1, "fala completa de 20s com min 30 deveria sobreviver (grace)");
})();

// --- montagem: pede folga acima do pedido (bug "pedi 3, veio 1") ---
(function () {
  assert.strictEqual(c._MONTAGE_REQUEST_BUFFER, 2, "buffer deveria ser 2 (espelho de core/objectives.py)");
  var requestCount = Math.min(3 + c._MONTAGE_REQUEST_BUFFER, c._MONTAGE_REQUEST_CAP);
  assert.strictEqual(requestCount, 5, "pedido de 3 deveria virar 5 candidatas (folga p/ filtro de espalhamento)");
  var capped = Math.min(9 + c._MONTAGE_REQUEST_BUFFER, c._MONTAGE_REQUEST_CAP);
  assert.strictEqual(capped, 10, "buffer nao deveria estourar o teto");
})();

console.log("OK: espelho timeline_map JS (remap + split + buildCutPlan + montagem + grace + folga)");
