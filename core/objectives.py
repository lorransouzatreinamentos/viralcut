"""
Objetivos 2 e 3 do VIRALCUT em Python (DaVinci). Espelham o core-cep.js (Node/Premiere):
 - montar falas (frankenbite): IA costura segmentos de varios momentos numa narrativa nova.
 - remover silencios: algoritmico, usa os gaps entre palavras.

Como no Premiere, a IA responde SOMENTE com IDs de segmento; o timecode vem sempre
das palavras reais (ver model.resolve). Timecodes aqui sao em SEGUNDOS de origem
(a transcricao e do arquivo-fonte), prontos para virar frames no adapter DaVinci.
"""
from __future__ import annotations

import json

import httpx

from core.config import settings
from core.model import TranscriptState

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
PADDING = 0.08

SYSTEM_PROMPT_MONTAGE = """Voce e um editor senior e montador narrativo. Sua especialidade e o FRANKENBITE: costurar falas de MOMENTOS BEM DISTANTES de um mesmo video em portugues para construir uma NARRATIVA NOVA, mais forte que qualquer trecho linear.

ENTRADA: transcricao SEGMENTADA (id, tempo, texto).
SAIDA: para cada montagem, SOMENTE: segments (LISTA ORDENADA de ids na ordem de reproducao, NAO cronologica), titulo, hook_first_3s, motivo, score (0-100). NUNCA escreva timestamps, apenas ids.

REGRA MAIS IMPORTANTE -- ESPALHAMENTO:
Os segmentos de UMA montagem TEM que vir de partes BEM DIFERENTES do video (comeco, meio E fim), nao de um mesmo trecho. Se voce pegar segmentos vizinhos/seguidos (ex: 12,13,14,15), isso NAO e montagem -- e so um corte linear, e esta ERRADO. O certo e SALTAR pelo video inteiro.
EXEMPLO DE UMA BOA MONTAGEM (repare como os ids/tempos pulam por todo o video):
  seg do minuto 0  ->  seg do minuto 2  ->  seg do minuto 0 de novo  ->  seg do minuto 3  ->  seg do minuto 1
Cada bloco vem de um lugar distinto; juntos formam um raciocinio novo.

ARCO (puxando de QUALQUER ponto do video):
1. GANCHO CONTRA-INTUITIVO: a afirmacao mais forte/surpreendente do video, mesmo que dita no meio ou no fim. Comece pelo pico.
2. DESENVOLVIMENTO: blocos de OUTROS momentos que sustentam/explicam o gancho.
3. PAYOFF: a virada ou frase-tapa que fecha e faz compartilhar.
A montagem soa como UMA fala continua e proposital, MAS as pecas vem de lugares distantes.

COERENCIA: cada salto so vale se houver continuidade logica, tematica, pergunta->resposta, ou contraste proposital. NUNCA deixe pronome orfao nem crie uma afirmacao que a pessoa NAO fez.
HONESTIDADE: recombine a ORDEM, nunca distorca o que a pessoa disse. Use apenas texto que existe na transcricao.
TAMANHO: de 4 a 8 blocos, CADA UM de um momento diferente do video.
So entregue montagens genuinamente mais fortes que um corte linear (score acima de ~65). Ordene do maior score para o menor. Use a ferramenta propose_montages."""

MONTAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "montagens": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "segments": {"type": "array", "items": {"type": "integer"}},
                    "titulo": {"type": "string"},
                    "hook_first_3s": {"type": "string"},
                    "motivo": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["segments", "titulo"],
            },
        }
    },
    "required": ["montagens"],
}


def _build_montage_prompt(transcript: TranscriptState, request_count: int, min_dur: float, max_dur: float) -> str:
    lines = [f"[seg {s.id} | {s.start:.1f}-{s.end:.1f}] {s.text}" for s in transcript.segments]
    return (
        f"GERE {request_count} PROPOSTAS DE MONTAGEM. DURACAO ALVO: {min_dur:.0f}-{max_dur:.0f}s.\n\n"
        f"IMPORTANTE: o sistema descarta automaticamente qualquer proposta cujos segmentos fiquem "
        f"concentrados numa mesma parte do video (isso NAO e frankenbite, e corte linear). Por isso "
        f"peca {request_count} propostas GENUINAMENTE DIFERENTES entre si -- nao repita o mesmo "
        f"conjunto de segmentos em duas propostas, e garanta que CADA proposta espalha por comeco, "
        f"meio e fim do video (nao so uma delas).\n\n"
        f"TRANSCRICAO (id | tempo | texto):\n" + "\n".join(lines) +
        "\n\nCrie montagens costurando segmentos de momentos diferentes numa narrativa nova e mais forte. "
        "Cada montagem e uma lista ORDENADA de ids (ordem de reproducao). Ordene por score."
    )


async def _call_openai_montage(transcript: TranscriptState, request_count: int, min_dur: float, max_dur: float) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY nao configurada")
    model = settings.llm_model if settings.llm_provider == "openai" else "gpt-4o"
    user = _build_montage_prompt(transcript, request_count, min_dur, max_dur)
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT_MONTAGE},
                    {"role": "user", "content": user},
                ],
                "tools": [{"type": "function", "function": {
                    "name": "propose_montages", "parameters": MONTAGE_SCHEMA}}],
                "tool_choice": {"type": "function", "function": {"name": "propose_montages"}},
            },
        )
    resp.raise_for_status()
    calls = resp.json()["choices"][0]["message"].get("tool_calls")
    if not calls:
        raise RuntimeError("OpenAI nao retornou montagens.")
    return json.loads(calls[0]["function"]["arguments"])


# Uma montagem (frankenbite) TEM que espalhar pelo video. Se os segmentos ficam
# grudados numa regiao, e um corte linear disfarcado -> descarta. Trava em CODIGO
# porque a IA tende a "jogar seguro" pegando trechos vizinhos.
MONTAGE_MIN_PIECES = 3      # menos que isso nao e montagem
MONTAGE_MIN_SPREAD = 0.35   # os trechos escolhidos cobrem >=35% da duracao do video


def _montage_is_spread(seg_ids: list[int], transcript: TranscriptState) -> bool:
    starts = []
    for sid in seg_ids:
        try:
            starts.append(transcript.segment_by_id(sid).start)
        except KeyError:
            pass
    if len(starts) < MONTAGE_MIN_PIECES:
        return False
    video_lo = min(s.start for s in transcript.segments)
    video_hi = max(s.end for s in transcript.segments)
    total = video_hi - video_lo
    if total <= 0:
        return True  # video degenerado (tudo no mesmo instante): nao trava
    return (max(starts) - min(starts)) / total >= MONTAGE_MIN_SPREAD


def _resolve_montages(raw: dict, transcript: TranscriptState) -> tuple[list[dict], int]:
    montages = []
    rejeitadas_agrupadas = 0
    for idx, m in enumerate(raw.get("montagens", [])):
        seg_ids = m.get("segments", [])
        # trava de espalhamento: montagem grudada numa regiao nao e frankenbite
        if not _montage_is_spread(seg_ids, transcript):
            rejeitadas_agrupadas += 1
            continue
        pieces = []
        for seg_id in seg_ids:
            try:
                seg = transcript.segment_by_id(seg_id)
            except KeyError:
                continue
            if not seg.word_ids:
                continue
            w0 = transcript.word_by_id(seg.word_ids[0])
            w1 = transcript.word_by_id(seg.word_ids[-1])
            pieces.append({"start": max(0.0, w0.start - PADDING), "end": w1.end + PADDING, "text": seg.text})
            if len(pieces) >= 8:
                break
        if len(pieces) < MONTAGE_MIN_PIECES:
            continue
        montages.append({
            "id": f"frk_{idx}",
            "titulo": m.get("titulo", f"Montagem {idx + 1}"),
            "hook_first_3s": m.get("hook_first_3s", ""),
            "motivo": m.get("motivo", ""),
            "score": int(m.get("score", 60)),
            "pieces": pieces,
            "text": "  //  ".join(p["text"] for p in pieces),
        })
    montages.sort(key=lambda x: x["score"], reverse=True)
    return montages, rejeitadas_agrupadas


# Pedir EXATAMENTE o numero de montagens que o usuario quer nao deixa margem: se
# a IA propor N e o filtro de espalhamento (MONTAGE_MIN_SPREAD) descartar 1 ou 2
# por ficarem concentradas numa mesma parte do video, sobra menos que o pedido
# sem chance de reposicao. Pedimos folga -- a IA gera mais candidatas do que o
# necessario, o codigo filtra e entrega as melhores ate o numero pedido.
MONTAGE_REQUEST_BUFFER = 2
MONTAGE_REQUEST_CAP = 10


async def extract_montages(
    transcript: TranscriptState, max_montages: int = 3, min_dur: float = 15, max_dur: float = 90,
) -> tuple[list[dict], dict]:
    """Retorna (montagens_entregues, meta). meta expoe QUANTO foi pedido vs
    sugerido vs valido vs entregue -- para a UI explicar por que veio menos que
    o pedido, em vez de simplesmente entregar menos em silencio."""
    if not transcript.segments:
        return [], {"requested": max_montages, "suggested": 0, "valid": 0, "delivered": 0, "descartadas_agrupadas": 0}

    request_count = min(max_montages + MONTAGE_REQUEST_BUFFER, MONTAGE_REQUEST_CAP)
    raw = await _call_openai_montage(transcript, request_count, min_dur, max_dur)
    montages, descartadas_agrupadas = _resolve_montages(raw, transcript)
    delivered = montages[:max_montages]
    meta = {
        "requested": max_montages,
        "suggested": len(raw.get("montagens", [])),
        "valid": len(montages),
        "delivered": len(delivered),
        "descartadas_agrupadas": descartadas_agrupadas,
    }
    return delivered, meta


# ---------------------------------------------------------------------------
# Remover silencios (algoritmico)
# ---------------------------------------------------------------------------

def detect_spoken_spans(transcript: TranscriptState, gap_threshold: float = 0.6) -> list[dict]:
    words = sorted(transcript.words, key=lambda w: w.start)
    spans: list[dict] = []
    cur = None
    for w in words:
        if cur is None:
            cur = {"start": w.start, "end": w.end}
        elif w.start - cur["end"] >= gap_threshold:
            spans.append(cur)
            cur = {"start": w.start, "end": w.end}
        else:
            cur["end"] = w.end
    if cur:
        spans.append(cur)
    return spans


# Padding ADAPTATIVO (ver core-cep.js, mesma logica). O `end` do Whisper e o fim
# do fonema, nao da cauda audivel -- medido com ffmpeg silencedetect, o som real
# continua ate ~160ms depois. Um padding fixo de 0.03s decepava a silaba final.
HEAD_PAD_MAX = 0.10
TAIL_PAD_MAX = 0.25
TAIL_PAD_RATIO = 0.8  # usa ate 80% da folga real -- nunca invade a proxima fala


def pad_spans(spans: list[dict], duration_sec: float) -> list[dict]:
    out = []
    for i, s in enumerate(spans):
        prev_end = spans[i - 1]["end"] if i > 0 else 0.0
        next_start = spans[i + 1]["start"] if i < len(spans) - 1 else (duration_sec or s["end"] + TAIL_PAD_MAX)

        headroom = max(0.0, s["start"] - prev_end)
        tailroom = max(0.0, next_start - s["end"])

        head = min(HEAD_PAD_MAX, headroom * 0.5)
        tail = min(TAIL_PAD_MAX, tailroom * TAIL_PAD_RATIO)

        out.append({"start": max(0.0, s["start"] - head), "end": s["end"] + tail})
    return out


def remove_silences(transcript: TranscriptState, duration_sec: float, gap_threshold: float = 0.6) -> dict:
    raw_spans = detect_spoken_spans(transcript, gap_threshold)
    spans = pad_spans(raw_spans, duration_sec)
    cuts = []
    kept = 0.0
    for i, s in enumerate(spans):
        st, en = s["start"], s["end"]
        kept += en - st
        cuts.append({"id": f"sil_{i}", "start": st, "end": en, "color": "Green"})
    original = duration_sec or (transcript.words[-1].end if transcript.words else kept)
    return {
        "spans": len(spans),
        "original_sec": original,
        "new_sec": kept,
        "saved_sec": max(0.0, original - kept),
        "gap_threshold": gap_threshold,
        "cuts": cuts,
    }
