/**
 * Espelho JS dos testes de tests/test_chunking.py. Erro real do usuario:
 * "Request too large for gpt-4o ... TPM: Limit 30000" ao extrair falas virais
 * no Premiere -- a conta tem TPM baixo e a transcricao inteira num prompt so
 * estourava. Rode com: node scripts/test_chunking_js.js
 */
const assert = require("assert");
const c = require("../premiere-panel/client/core-cep.js");

function seg(id, textLen) {
  return { id, start: id * 5.0, end: id * 5.0 + 4.0, text: "x".repeat(textLen), word_ids: [id] };
}
function segs(n, textLen) {
  const out = [];
  for (let i = 0; i < n; i++) out.push(seg(i, textLen));
  return out;
}

// --- chunkSegments (pure) ----------------------------------------------------
(function () {
  const short = segs(5, 20);
  const chunks = c._chunkSegments(short);
  assert.strictEqual(chunks.length, 1, "transcricao curta deveria ficar num unico lote");

  const long = segs(200, 200);
  const chunksLong = c._chunkSegments(long);
  assert.ok(chunksLong.length > 1, "transcricao longa deveria ser dividida");
  const flat = chunksLong.flat().map((s) => s.id);
  assert.deepStrictEqual(flat, long.map((s) => s.id), "nenhum segmento pode se perder / fora de ordem");

  const huge = [seg(0, 50000)];
  const chunksHuge = c._chunkSegments(huge);
  assert.strictEqual(chunksHuge.length, 1, "1 segmento gigante nao pode travar/quebrar o texto");
  assert.strictEqual(chunksHuge[0].length, 1);
})();

// --- gptCallWithRetry: retry em 429, nao em outros erros ---------------------
(function fakeResponse(status, body) {
  return { ok: status < 400, status: status, json: async () => body };
})();

function mockFetchSequence(responses) {
  let i = 0;
  global.fetch = async function () {
    const r = responses[Math.min(i, responses.length - 1)];
    i++;
    return { ok: r.status < 400, status: r.status, json: async () => r.body };
  };
  return function callCount() { return i; };
}

function toolCallBody(argsObj) {
  return { choices: [{ message: { tool_calls: [{ function: { arguments: JSON.stringify(argsObj) } }] } }] };
}

async function main() {
  c.__setSleepForTests(function () { return Promise.resolve(); }); // sem espera real

  // 429, 429, depois 200 -- deveria dar certo na 3a tentativa
  {
    const getCalls = mockFetchSequence([
      { status: 429, body: { error: { message: "rate limited" } } },
      { status: 429, body: { error: { message: "rate limited" } } },
      { status: 200, body: toolCallBody({ clips: [] }) },
    ]);
    const out = await c._gptCallWithRetry("fake-key", "sys", "user", "propose_clips", {}, null, 0.1);
    assert.deepStrictEqual(out, { clips: [] });
    assert.strictEqual(getCalls(), 3, "deveria ter tentado 3 vezes ate dar certo");
  }

  // sempre 429 -- desiste apos as tentativas, propaga o erro
  {
    mockFetchSequence([{ status: 429, body: { error: { message: "rate limited" } } }]);
    let threw = false;
    try {
      await c._gptCallWithRetry("fake-key", "sys", "user", "propose_clips", {}, null, 0.1);
    } catch (e) {
      threw = true;
      assert.strictEqual(e.status, 429);
    }
    assert.ok(threw, "deveria ter desistido e propagado o erro 429");
  }

  // erro que NAO e 429 -- falha na hora, sem retry
  {
    const getCalls = mockFetchSequence([{ status: 400, body: { error: { message: "schema invalido" } } }]);
    let threw = false;
    try {
      await c._gptCallWithRetry("fake-key", "sys", "user", "propose_clips", {}, null, 0.1);
    } catch (e) {
      threw = true;
    }
    assert.ok(threw);
    assert.strictEqual(getCalls(), 1, "erro que nao e 429 nao deveria ser retentado");
  }

  console.log("OK: chunking + retry-em-429 (espelho JS de test_chunking.py)");
}

main().catch(function (e) { console.error("FALHOU:", e); process.exit(1); });
