"""
Duracao dos cortes virais: a faixa min/max e um ALVO com FOLGA, nao corte seco.

Pedido do usuario: "as falas tem que vir sem corte, na integra dentro das regras
de tempo, podendo sim ser mais ou menos do que o tempo definido se for preciso."
Ou seja: uma fala COMPLETA um pouco fora da faixa e MANTIDA; so fragmentos e
trechos arrastados absurdos sao descartados.
"""
from core.model import Segment, TranscriptState, Word
from core.viral import _resolve_and_filter


def _transcript() -> TranscriptState:
    """3 segmentos: fragmento (~1.4s), fala completa (~20s) e trecho longo (~90s)."""
    words = [
        Word(id=0, text="ola", start=0.0, end=0.6),
        Word(id=1, text="mundo", start=0.6, end=1.4),           # seg 0: ~1.4s
        Word(id=2, text="isso", start=10.0, end=12.0),
        Word(id=3, text="aqui", start=12.0, end=30.0),          # seg 1: ~20s
        Word(id=4, text="muito", start=40.0, end=80.0),
        Word(id=5, text="longo", start=80.0, end=130.0),        # seg 2: ~90s
    ]
    segments = [
        Segment(id=0, start=0.0, end=1.4, text="ola mundo", word_ids=[0, 1]),
        Segment(id=1, start=10.0, end=30.0, text="isso aqui", word_ids=[2, 3]),
        Segment(id=2, start=40.0, end=130.0, text="muito longo", word_ids=[4, 5]),
    ]
    return TranscriptState(words=words, segments=segments)


def _raw(seg_id: int, titulo: str):
    return {"start_seg_id": seg_id, "end_seg_id": seg_id, "titulo": titulo, "score": 80}


def test_fala_completa_um_pouco_abaixo_do_min_SOBREVIVE():
    """O ponto central do pedido: fala completa de ~20s, com min de 30s, NAO e
    descartada -- a folga (50%) mantem quem esta perto da faixa."""
    t = _transcript()
    raw_input = {"clips": [_raw(1, "fala completa 20s")]}

    clips, rejected = _resolve_and_filter(raw_input, t, min_score=0, max_clips=10, min_dur=30.0, max_dur=40.0)

    assert [c.titulo for c in clips] == ["fala completa 20s"], "descartou uma fala completa por 10s de diferenca"
    assert rejected == {"curto": 0, "longo": 0}


def test_descarta_fragmento_curto_demais():
    """~1.4s com min de 30s (folga = 15s): abaixo da folga -> fragmento, descarta."""
    t = _transcript()
    clips, rejected = _resolve_and_filter({"clips": [_raw(0, "fragmento")]}, t,
                                          min_score=0, max_clips=10, min_dur=30.0, max_dur=90.0)
    assert clips == []
    assert rejected["curto"] == 1


def test_descarta_trecho_arrastado_alem_da_folga():
    """~90s com max de 40s (folga = 60s): passou muito -> descarta."""
    t = _transcript()
    clips, rejected = _resolve_and_filter({"clips": [_raw(2, "arrastado")]}, t,
                                          min_score=0, max_clips=10, min_dur=5.0, max_dur=40.0)
    assert clips == []
    assert rejected["longo"] == 1


def test_dentro_da_faixa_passa():
    t = _transcript()
    clips, rejected = _resolve_and_filter({"clips": [_raw(1, "no ponto")]}, t,
                                          min_score=0, max_clips=10, min_dur=15.0, max_dur=30.0)
    assert [c.titulo for c in clips] == ["no ponto"]
    assert rejected == {"curto": 0, "longo": 0}


def test_sem_faixa_definida_nao_filtra_nada():
    """min_dur/max_dur em 0 = desligado (retrocompatibilidade)."""
    t = _transcript()
    clips, rejected = _resolve_and_filter({"clips": [_raw(0, "frag"), _raw(2, "longo")]}, t,
                                          min_score=0, max_clips=10, min_dur=0.0, max_dur=0.0)
    assert len(clips) == 2
    assert rejected == {"curto": 0, "longo": 0}
