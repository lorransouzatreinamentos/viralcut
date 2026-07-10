"""
Montagem (frankenbite) tem que ESPALHAR pelo video. O usuario reclamou que ela
estava "fazendo cortes numa fala meio que na mesma sequencia" -- ou seja, pegando
segmentos grudados numa regiao em vez de saltar por todo o video.

Estes testes travam a regra: montagem com segmentos agrupados numa regiao e
rejeitada; montagem que salta por comeco/meio/fim passa.
"""
from core.model import Segment, TranscriptState, Word
from core.objectives import _montage_is_spread, _resolve_montages


def _transcript(n=20, seg_len=5.0):
    """n segmentos de seg_len segundos cada, cobrindo 0..n*seg_len."""
    words, segments = [], []
    for i in range(n):
        s, e = i * seg_len, i * seg_len + seg_len
        words.append(Word(id=i, text=f"w{i}", start=s, end=e - 0.1))
        segments.append(Segment(id=i, start=s, end=e - 0.1, text=f"fala {i}", word_ids=[i]))
    return TranscriptState(words=words, segments=segments)


def test_montagem_agrupada_numa_regiao_e_rejeitada():
    """Segmentos vizinhos (5,6,7,8) num video de 20 = corte linear disfarcado."""
    t = _transcript(n=20)
    assert _montage_is_spread([5, 6, 7, 8], t) is False


def test_montagem_que_salta_pelo_video_passa():
    """Comeco, meio e fim (2, 9, 17) cobrem ~75% do video -> frankenbite de verdade."""
    t = _transcript(n=20)
    assert _montage_is_spread([2, 9, 17], t) is True


def test_menos_de_tres_pecas_nao_e_montagem():
    t = _transcript(n=20)
    assert _montage_is_spread([1, 18], t) is False  # so 2 pecas, mesmo espalhadas


def test_resolve_descarta_agrupada_e_mantem_espalhada():
    t = _transcript(n=20)
    raw = {"montagens": [
        {"segments": [1, 2, 3, 4], "titulo": "agrupada", "score": 90},   # grudada -> fora
        {"segments": [0, 8, 15, 19], "titulo": "espalhada", "score": 80},  # salta -> ok
    ]}
    montages, rejeitadas = _resolve_montages(raw, t)

    assert [m["titulo"] for m in montages] == ["espalhada"]
    assert rejeitadas == 1
