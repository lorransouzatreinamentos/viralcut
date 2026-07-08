"""
IA de extracao de cortes virais (ver PLANO_MESTRE.md secoes 1.1, 9.2, 12.1).

Esta e a correcao do problema que o usuario marcou como PRIMORDIAL: no FastVideo,
a IA "copiava" um numero de timestamp que ela mesma lia do prompt, e um snap
frouxo (2.5s de tolerancia) mascarava quando ela errava. Aqui:

  1. A IA e forcada a usar uma tool call cujo schema (LLMHighlightBatch) e o
     MESMO pydantic model usado na validacao -- nao ha campo start/end nele,
     entao a IA fisicamente nao tem onde escrever um timestamp.
  2. O timecode final vem SEMPRE de resolve_highlight(), que le a palavra
     real na transcricao. O numero que a IA "pensa" nunca e usado.
  3. Cortes com ID de segmento invalido sao descartados individualmente
     (nao derrubam a extracao inteira -- ver secao 16).
"""
from __future__ import annotations

import httpx
from pydantic import BaseModel, ValidationError

from core.config import settings
from core.model import HighlightClip, LLMHighlightRaw, TranscriptState, resolve_highlight

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = """Voce e um editor especialista em conteudo viral para redes sociais (Reels, TikTok, Shorts).
Recebe a transcricao segmentada de um video longo e identifica os melhores trechos para cortes virais.
Voce NAO escreve timestamps. Voce referencia apenas os IDs de segmento fornecidos.
Use SEMPRE a ferramenta fornecida para responder — nunca responda em texto solto."""


class LLMHighlightBatch(BaseModel):
    """Wrapper de saida da IA. Vira o input_schema da tool call (ver extract_viral_clips)
    -- mesmo pydantic model usado para validar a resposta, entao schema e validacao
    nunca podem divergir um do outro."""

    clips: list[LLMHighlightRaw]


def _build_user_prompt(
    transcript: TranscriptState, min_score: int, max_clips: int, min_dur: float, max_dur: float
) -> str:
    lines = [f"[seg {s.id} | {s.start:.2f}-{s.end:.2f}] {s.text}" for s in transcript.segments]
    return f"""DURACAO ALVO DOS CORTES: {min_dur:.0f}-{max_dur:.0f} segundos
N. MAXIMO DE CORTES: {max_clips}
SCORE MINIMO: {min_score}

TRANSCRICAO (cada linha = um segmento com id, tempo e texto):
{chr(10).join(lines)}

TAREFA:
Selecione os melhores trechos para cortes virais. Para cada corte, avalie:
- HOOK: os primeiros ~3s prendem a atencao e conectam ao tema?
- FLOW: o corte tem narrativa completa (comeco-meio-fim) e se entende sem contexto externo?
- EMOCAO/CONTRAINTUICAO: ha pico emocional, tensao ou quebra de expectativa?

Cada corte deve comecar e terminar em fronteiras de segmento (use os IDs).
Ordene por potencial viral (score 0-100). NAO inclua cortes com score < {min_score}."""


def _extract_tool_use_input(data: dict) -> dict:
    tool_use = next((b for b in data.get("content", []) if b.get("type") == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("Resposta da IA nao contem tool_use — nada para processar.")
    return tool_use["input"]


def _resolve_and_filter(
    raw_input: dict, transcript: TranscriptState, min_score: int, max_clips: int,
    min_dur: float = 0.0, max_dur: float = 0.0,
) -> tuple[list[HighlightClip], dict]:
    """Enforcement de duracao em CODIGO, nao so no prompt -- a IA nao respeita a
    faixa de forma confiavel, e era isso que gerava 'corte de 15s sem sentido'.
    Retorna (clips, rejeitados)."""
    try:
        batch = LLMHighlightBatch.model_validate(raw_input)
    except ValidationError as e:
        raise RuntimeError(f"IA devolveu formato invalido: {e}") from e

    clips: list[HighlightClip] = []
    rejected = {"curto": 0, "longo": 0}
    for i, raw in enumerate(batch.clips):
        try:
            clip = resolve_highlight(raw, transcript, clip_id=f"vir_{i:03d}")
        except (KeyError, ValueError):
            # ID de segmento inexistente/invalido — descarta so este corte (secao 16)
            continue
        if clip.score < min_score:
            continue
        dur = clip.end - clip.start
        if min_dur and dur < min_dur:
            rejected["curto"] += 1
            continue
        if max_dur and dur > max_dur:
            rejected["longo"] += 1
            continue
        clips.append(clip)

    clips.sort(key=lambda c: c.score, reverse=True)
    return clips[:max_clips], rejected


async def _call_anthropic(
    transcript: TranscriptState, min_score: int, max_clips: int, min_dur: float, max_dur: float
) -> dict:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY nao configurada (.env)")

    user_prompt = _build_user_prompt(transcript, min_score, max_clips, min_dur, max_dur)
    tool_schema = LLMHighlightBatch.model_json_schema()

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.llm_model,
                "max_tokens": 4096,
                "temperature": 0.1,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_prompt}],
                "tools": [
                    {
                        "name": "propose_clips",
                        "description": "Propoe cortes virais referenciando IDs de segmento existentes.",
                        "input_schema": tool_schema,
                    }
                ],
                "tool_choice": {"type": "tool", "name": "propose_clips"},
            },
        )
    resp.raise_for_status()
    return resp.json()


async def _call_openai(
    transcript: TranscriptState, min_score: int, max_clips: int, min_dur: float, max_dur: float
) -> dict:
    """Mesma estrategia do Anthropic: function calling forcado com o schema pydantic.
    O modelo so pode responder chamando propose_clips (sem campo de timestamp)."""
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY nao configurada (.env)")

    user_prompt = _build_user_prompt(transcript, min_score, max_clips, min_dur, max_dur)
    model = settings.llm_model if settings.llm_provider == "openai" else "gpt-4o"

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            OPENAI_URL,
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "propose_clips",
                            "description": "Propoe cortes virais referenciando IDs de segmento existentes.",
                            "parameters": LLMHighlightBatch.model_json_schema(),
                        },
                    }
                ],
                "tool_choice": {"type": "function", "function": {"name": "propose_clips"}},
            },
        )
    resp.raise_for_status()
    data = resp.json()
    tool_calls = data["choices"][0]["message"].get("tool_calls")
    if not tool_calls:
        raise RuntimeError("OpenAI nao retornou tool_call — nada para processar.")
    import json
    return json.loads(tool_calls[0]["function"]["arguments"])


async def extract_viral_clips(
    transcript: TranscriptState,
    min_score: int = 50,
    max_clips: int = 10,
    min_dur: float = 30.0,
    max_dur: float = 90.0,
) -> tuple[list[HighlightClip], dict]:
    """Retorna (clips, rejeitados). A faixa de duracao e aplicada em codigo."""
    if not transcript.segments:
        return [], {"curto": 0, "longo": 0}

    if settings.llm_provider == "openai":
        raw_input = await _call_openai(transcript, min_score, max_clips, min_dur, max_dur)
    else:
        data = await _call_anthropic(transcript, min_score, max_clips, min_dur, max_dur)
        raw_input = _extract_tool_use_input(data)
    return _resolve_and_filter(raw_input, transcript, min_score, max_clips, min_dur, max_dur)
