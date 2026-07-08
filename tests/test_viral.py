"""
Testes de core/viral.py. Nao chamam a API da Anthropic de verdade -- testam
o pipeline de prompt/parse/resolve/filtro com fixtures que simulam uma
resposta real da tool call, provando que a correcao do bug primordial
(PLANO_MESTRE.md 1.1) se sustenta ate a ponta do fluxo de extracao.
"""
import pytest

from core.model import Segment, TranscriptState, Word
from core.viral import (
    LLMHighlightBatch,
    _build_user_prompt,
    _extract_tool_use_input,
    _resolve_and_filter,
)


def _sample_transcript() -> TranscriptState:
    words = [
        Word(id=0, text="todo", start=12.00, end=12.20),
        Word(id=1, text="mundo", start=12.20, end=12.50),
        Word(id=2, text="faz", start=12.50, end=12.70),
        Word(id=3, text="isso", start=12.70, end=12.95),
        Word(id=4, text="errado", start=12.95, end=13.40),
        Word(id=5, text="hoje", start=50.00, end=50.30),
        Word(id=6, text="vamos", start=50.30, end=50.60),
        Word(id=7, text="mudar", start=50.60, end=51.00),
    ]
    segments = [
        Segment(id=0, start=12.00, end=12.50, text="todo mundo", word_ids=[0, 1]),
        Segment(id=1, start=12.50, end=13.40, text="faz isso errado", word_ids=[2, 3, 4]),
        Segment(id=2, start=50.00, end=51.00, text="hoje vamos mudar", word_ids=[5, 6, 7]),
    ]
    return TranscriptState(words=words, segments=segments)


def test_build_user_prompt_includes_segments_and_constraints():
    transcript = _sample_transcript()
    prompt = _build_user_prompt(transcript, min_score=60, max_clips=5, min_dur=20.0, max_dur=90.0)

    assert "[seg 0 | 12.00-12.50] todo mundo" in prompt
    assert "[seg 2 | 50.00-51.00] hoje vamos mudar" in prompt
    assert "SCORE MINIMO: 60" in prompt
    assert "N. MAXIMO DE CORTES: 5" in prompt


def test_extract_tool_use_input_finds_tool_use_block():
    data = {
        "content": [
            {"type": "text", "text": "algum texto irrelevante"},
            {"type": "tool_use", "name": "propose_clips", "input": {"clips": []}},
        ]
    }
    assert _extract_tool_use_input(data) == {"clips": []}


def test_extract_tool_use_input_raises_when_missing():
    with pytest.raises(RuntimeError, match="tool_use"):
        _extract_tool_use_input({"content": [{"type": "text", "text": "sem tool"}]})


def test_resolve_and_filter_derives_real_timecode_not_llm_number():
    """O teste central: a resposta da IA so tem IDs; o timecode do clip final
    vem das palavras reais, nao de nada que a IA tenha 'imaginado'."""
    transcript = _sample_transcript()
    raw_input = {
        "clips": [
            {"start_seg_id": 0, "end_seg_id": 1, "titulo": "O erro", "score": 87},
        ]
    }

    clips, _ = _resolve_and_filter(raw_input, transcript, min_score=50, max_clips=10)

    assert len(clips) == 1
    assert clips[0].start == pytest.approx(12.00 - 0.08)  # padding default de resolve_highlight
    assert clips[0].end == pytest.approx(13.40 + 0.08)
    assert clips[0].titulo == "O erro"


def test_resolve_and_filter_rejects_score_below_minimum():
    transcript = _sample_transcript()
    raw_input = {
        "clips": [
            {"start_seg_id": 0, "end_seg_id": 1, "titulo": "Fraco", "score": 30},
            {"start_seg_id": 2, "end_seg_id": 2, "titulo": "Forte", "score": 80},
        ]
    }

    clips, _ = _resolve_and_filter(raw_input, transcript, min_score=50, max_clips=10)

    assert len(clips) == 1
    assert clips[0].titulo == "Forte"


def test_resolve_and_filter_discards_invalid_segment_id_without_crashing():
    """Edge case da secao 16: IA aluciona um ID que nao existe -- descarta so aquele
    corte, nao derruba a extracao inteira."""
    transcript = _sample_transcript()
    raw_input = {
        "clips": [
            {"start_seg_id": 0, "end_seg_id": 999, "titulo": "ID invalido", "score": 90},
            {"start_seg_id": 2, "end_seg_id": 2, "titulo": "Valido", "score": 70},
        ]
    }

    clips, _ = _resolve_and_filter(raw_input, transcript, min_score=50, max_clips=10)

    assert len(clips) == 1
    assert clips[0].titulo == "Valido"


def test_resolve_and_filter_respects_max_clips_and_sorts_by_score():
    transcript = _sample_transcript()
    raw_input = {
        "clips": [
            {"start_seg_id": 0, "end_seg_id": 0, "titulo": "A", "score": 60},
            {"start_seg_id": 1, "end_seg_id": 1, "titulo": "B", "score": 90},
            {"start_seg_id": 2, "end_seg_id": 2, "titulo": "C", "score": 75},
        ]
    }

    clips, _ = _resolve_and_filter(raw_input, transcript, min_score=0, max_clips=2)

    assert len(clips) == 2
    assert [c.titulo for c in clips] == ["B", "C"]  # ordenado por score desc, top 2


def test_llm_batch_schema_has_no_timestamp_field():
    """Prova estrutural: o schema JSON exposto pra IA (tool input_schema) nunca
    tem 'start'/'end' numerico em lugar nenhum -- nao ha campo pra copiar errado."""
    schema = LLMHighlightBatch.model_json_schema()
    clip_schema = schema["$defs"]["LLMHighlightRaw"]
    assert "start" not in clip_schema["properties"]
    assert "end" not in clip_schema["properties"]
    assert clip_schema.get("additionalProperties") is False


def test_llm_highlight_batch_rejects_smuggled_timestamp_via_validate():
    with pytest.raises(Exception):  # pydantic ValidationError
        LLMHighlightBatch.model_validate(
            {"clips": [{"start_seg_id": 0, "end_seg_id": 1, "titulo": "x", "start": 125.3}]}
        )
