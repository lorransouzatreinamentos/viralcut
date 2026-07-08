"""
Trava o enforcement de duracao minima/maxima dos cortes.

Motivo: a IA nao respeita a faixa de duracao de forma confiavel, mesmo com o
prompt mandando. O resultado eram "cortes de 15s sem sentido". O filtro real
tem que estar em CODIGO -- e isto e o que estes testes garantem.
"""
from core.model import Segment, TranscriptState, Word
from core.viral import _resolve_and_filter


def _transcript() -> TranscriptState:
    """3 segmentos: um curto (~1.4s), um medio (~10s) e um longo (~60s)."""
    words = [
        # seg 0: 0.0 -> 1.4  (curto)
        Word(id=0, text="ola", start=0.0, end=0.6),
        Word(id=1, text="mundo", start=0.6, end=1.4),
        # seg 1: 10.0 -> 20.0 (medio)
        Word(id=2, text="isso", start=10.0, end=12.0),
        Word(id=3, text="aqui", start=12.0, end=20.0),
        # seg 2: 30.0 -> 90.0 (longo)
        Word(id=4, text="muito", start=30.0, end=60.0),
        Word(id=5, text="longo", start=60.0, end=90.0),
    ]
    segments = [
        Segment(id=0, start=0.0, end=1.4, text="ola mundo", word_ids=[0, 1]),
        Segment(id=1, start=10.0, end=20.0, text="isso aqui", word_ids=[2, 3]),
        Segment(id=2, start=30.0, end=90.0, text="muito longo", word_ids=[4, 5]),
    ]
    return TranscriptState(words=words, segments=segments)


def _raw(seg_id: int, titulo: str):
    return {"start_seg_id": seg_id, "end_seg_id": seg_id, "titulo": titulo, "score": 80}


def test_descarta_corte_menor_que_minimo():
    """Corte de ~1.4s com minimo de 30s -> descartado, e contabilizado."""
    t = _transcript()
    raw_input = {"clips": [_raw(0, "curto demais")]}

    clips, rejected = _resolve_and_filter(raw_input, t, min_score=0, max_clips=10, min_dur=30.0, max_dur=90.0)

    assert clips == []
    assert rejected["curto"] == 1


def test_descarta_corte_maior_que_maximo():
    """Corte de ~60s com maximo de 40s -> descartado."""
    t = _transcript()
    raw_input = {"clips": [_raw(2, "longo demais")]}

    clips, rejected = _resolve_and_filter(raw_input, t, min_score=0, max_clips=10, min_dur=5.0, max_dur=40.0)

    assert clips == []
    assert rejected["longo"] == 1


def test_mantem_corte_dentro_da_faixa():
    t = _transcript()
    raw_input = {"clips": [_raw(1, "no ponto")]}

    clips, rejected = _resolve_and_filter(raw_input, t, min_score=0, max_clips=10, min_dur=5.0, max_dur=30.0)

    assert len(clips) == 1
    assert clips[0].titulo == "no ponto"
    assert rejected == {"curto": 0, "longo": 0}


def test_filtra_apenas_os_fora_da_faixa():
    """Curto e longo caem; o do meio sobrevive. Um corte ruim nao derruba os bons."""
    t = _transcript()
    raw_input = {"clips": [_raw(0, "curto"), _raw(1, "bom"), _raw(2, "longo")]}

    clips, rejected = _resolve_and_filter(raw_input, t, min_score=0, max_clips=10, min_dur=5.0, max_dur=30.0)

    assert [c.titulo for c in clips] == ["bom"]
    assert rejected["curto"] == 1
    assert rejected["longo"] == 1


def test_sem_faixa_definida_nao_filtra_nada():
    """min_dur/max_dur em 0 = desligado (retrocompatibilidade)."""
    t = _transcript()
    raw_input = {"clips": [_raw(0, "curto"), _raw(2, "longo")]}

    clips, rejected = _resolve_and_filter(raw_input, t, min_score=0, max_clips=10, min_dur=0.0, max_dur=0.0)

    assert len(clips) == 2
    assert rejected == {"curto": 0, "longo": 0}
