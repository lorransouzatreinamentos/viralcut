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

console.log("OK: espelho timeline_map JS (remap + split + buildCutPlan)");
