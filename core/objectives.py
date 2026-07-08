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

SYSTEM_PROMPT_MONTAGE = """Voce e um editor senior e montador narrativo de cortes virais. Sua especialidade e o FRANKENBITE: costurar falas de MOMENTOS DIFERENTES de um mesmo video em portugues para construir uma narrativa nova, mais forte e mais viral do que qualquer trecho linear.

ENTRADA: transcricao SEGMENTADA (id, tempo, texto).
SAIDA: para cada montagem, SOMENTE: segments (LISTA ORDENADA de ids na ordem de reproducao, NAO cronologica), titulo, hook_first_3s, motivo, score (0-100). NUNCA escreva timestamps, apenas ids.

ARCO OBRIGATORIO (puxando de qualquer ponto do video):
1. GANCHO CONTRA-INTUITIVO: a afirmacao mais forte/surpreendente do video, mesmo que dita no meio ou no fim. Comece pelo pico.
2. DESENVOLVIMENTO: os blocos que sustentam/explicam o gancho, na ordem que constroi melhor o argumento.
3. PAYOFF: a virada ou frase-tapa que fecha e faz compartilhar.
A montagem tem que soar como UMA fala continua e proposital.

COERENCIA: cada salto entre segmentos so vale se houver continuidade logica, tematica, pergunta->resposta, ou contraste proposital que faz sentido. NUNCA junte blocos onde um pronome fica orfao, o assunto muda de forma confusa, ou a costura cria uma afirmacao que a pessoa NAO fez.
HONESTIDADE: recombine a ORDEM, mas nunca distorca o que a pessoa disse. Use apenas texto que existe na transcricao.
TAMANHO: cada montagem tem de 3 a 6 blocos (MAXIMO 6). Menos e mais.
So entregue montagens genuinamente mais fortes que um corte linear (score acima de ~65). Prefira 1-3 montagens excelentes. Ordene do maior score para o menor. Use a ferramenta propose_montages."""

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


def _build_montage_prompt(transcript: TranscriptState, max_montages: int, min_dur: float, max_dur: float) -> str:
    lines = [f"[seg {s.id} | {s.start:.1f}-{s.end:.1f}] {s.text}" for s in transcript.segments]
    return (
        f"MAX MONTAGENS: {max_montages}. DURACAO ALVO: {min_dur:.0f}-{max_dur:.0f}s.\n\n"
        f"TRANSCRICAO (id | tempo | texto):\n" + "\n".join(lines) +
        "\n\nCrie montagens costurando segmentos de momentos diferentes numa narrativa nova e mais forte. "
        "Cada montagem e uma lista ORDENADA de ids (ordem de reproducao). Ordene por score."
    )


async def _call_openai_montage(transcript: TranscriptState, max_montages: int, min_dur: float, max_dur: float) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY nao configurada")
    model = settings.llm_model if settings.llm_provider == "openai" else "gpt-4o"
    user = _build_montage_prompt(transcript, max_montages, min_dur, max_dur)
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


def _resolve_montages(raw: dict, transcript: TranscriptState) -> list[dict]:
    montages = []
    for idx, m in enumerate(raw.get("montagens", [])):
        pieces = []
        for seg_id in m.get("segments", []):
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
        if len(pieces) < 2:
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
    return montages


async def extract_montages(transcript: TranscriptState, max_montages: int = 3, min_dur: float = 15, max_dur: float = 90) -> list[dict]:
    if not transcript.segments:
        return []
    raw = await _call_openai_montage(transcript, max_montages, min_dur, max_dur)
    return _resolve_montages(raw, transcript)


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
