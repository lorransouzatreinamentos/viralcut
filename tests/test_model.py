"""
Testes de invariante do modelo. O mais importante deste arquivo e
test_llm_cannot_smuggle_a_timestamp: prova que o bug primordial do
FastVideo (IA "copiando" um numero de timecode errado) e estruturalmente
impossivel aqui, nao apenas evitado por instrucao de prompt.
"""
import pytest
from pydantic import ValidationError

from core.model import (
    HighlightClip,
    LLMHighlightRaw,
    Segment,
    TranscriptState,
    Word,
    resolve_highlight,
)


def _sample_transcript() -> TranscriptState:
    # "todo mundo faz isso errado" — 5 palavras, 2 segmentos
    words = [
        Word(id=0, text="todo", start=12.00, end=12.20),
        Word(id=1, text="mundo", start=12.20, end=12.50),
        Word(id=2, text="faz", start=12.50, end=12.70),
        Word(id=3, text="isso", start=12.70, end=12.95),
        Word(id=4, text="errado", start=12.95, end=13.40),
    ]
    segments = [
        Segment(id=0, start=12.00, end=12.50, text="todo mundo", word_ids=[0, 1]),
        Segment(id=1, start=12.50, end=13.40, text="faz isso errado", word_ids=[2, 3, 4]),
    ]
    return TranscriptState(words=words, segments=segments)


def test_llm_cannot_smuggle_a_timestamp():
    """O schema que a IA preenche nao aceita start/end — extra='forbid'."""
    with pytest.raises(ValidationError):
        LLMHighlightRaw(
            start_seg_id=0,
            end_seg_id=1,
            titulo="Teste",
            start=125.3,  # campo nao existe no schema -> deve falhar
        )


def test_resolve_highlight_derives_timecode_from_words_not_llm():
    transcript = _sample_transcript()
    raw = LLMHighlightRaw(start_seg_id=0, end_seg_id=1, titulo="O erro", score=87)

    clip = resolve_highlight(raw, transcript, clip_id="vir_001")

    # word ids vem EXATAMENTE das palavras (word 0 e word 4), nao de nada que a IA "escreveu"
    assert clip.start_word_id == 0
    assert clip.end_word_id == 4
    assert clip.score == 87
    # start/end sao DERIVADOS das palavras (com padding adaptativo), nunca de um numero da IA:
    # start fica na janela [word0.start - HEAD_MAX, word0.start]; end em [word4.end, word4.end + TAIL_MAX]
    assert 12.00 - 0.10 <= clip.start <= 12.00
    assert 13.40 <= clip.end <= 13.40 + 0.25


def test_resolve_highlight_padding_adaptativo_estende_a_cauda():
    """A cauda estende alem do fim do fonema (anti 'come palavras'), respeitando o teto."""
    transcript = _sample_transcript()
    raw = LLMHighlightRaw(start_seg_id=0, end_seg_id=1, titulo="O erro")

    clip = resolve_highlight(raw, transcript, clip_id="vir_001")

    assert clip.end > 13.40, "cauda nao foi estendida -- comeria a silaba final"
    assert clip.end <= 13.40 + 0.25 + 1e-9
    assert clip.start < 12.00, "head nao aplicado"


def test_resolve_highlight_nunca_fica_negativo():
    """Video que comeca logo no inicio: o head nunca empurra o start abaixo de 0."""
    words = [Word(id=0, text="ja", start=0.05, end=0.30)]
    segs = [Segment(id=0, start=0.05, end=0.30, text="ja", word_ids=[0])]
    transcript = TranscriptState(words=words, segments=segs)
    raw = LLMHighlightRaw(start_seg_id=0, end_seg_id=0, titulo="Inicio")

    clip = resolve_highlight(raw, transcript, clip_id="vir_001")

    assert clip.start >= 0.0


def test_llm_raw_rejects_end_before_start():
    with pytest.raises(ValidationError):
        LLMHighlightRaw(start_seg_id=5, end_seg_id=1, titulo="Invalido")


def test_resolve_highlight_unknown_segment_raises():
    transcript = _sample_transcript()
    raw = LLMHighlightRaw(start_seg_id=0, end_seg_id=99, titulo="Segmento inexistente")

    with pytest.raises(KeyError):
        resolve_highlight(raw, transcript, clip_id="vir_001")


def test_highlight_clip_rejects_end_before_start():
    with pytest.raises(ValidationError):
        HighlightClip(
            id="x",
            titulo="x",
            start_word_id=4,
            end_word_id=0,
            start=10.0,
            end=5.0,
            score=50,
        )


def test_word_rejects_end_before_start():
    with pytest.raises(ValidationError):
        Word(id=0, text="x", start=5.0, end=4.0)


def test_transcript_state_rejects_duplicate_word_ids():
    with pytest.raises(ValidationError):
        TranscriptState(
            words=[
                Word(id=0, text="a", start=0.0, end=0.5),
                Word(id=0, text="b", start=0.5, end=1.0),
            ]
        )
