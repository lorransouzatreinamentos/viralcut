"""
Montagem (frankenbite) tem que ESPALHAR pelo video. O usuario reclamou que ela
estava "fazendo cortes numa fala meio que na mesma sequencia" -- ou seja, pegando
segmentos grudados numa regiao em vez de saltar por todo o video.

Estes testes travam a regra: montagem com segmentos agrupados numa regiao e
rejeitada; montagem que salta por comeco/meio/fim passa.
"""
import asyncio

from core.model import Segment, TranscriptState, Word
from core.objectives import (
    MONTAGE_REQUEST_BUFFER,
    _montage_is_spread,
    _resolve_montages,
    _segments_too_similar,
    extract_montages,
)


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


# --- "ainda agrupando momentos similares": buraco do span (adjacencia) -------

def test_span_alto_mas_segmentos_colados_e_rejeitada():
    """O buraco do filtro antigo: [0,1,2,18] cobre quase todo o video (span alto)
    mas 0,1,2 sao falas SEGUIDAS -- e um corte linear + 1 outlier, nao frankenbite."""
    t = _transcript(n=20)
    assert _montage_is_spread([0, 1, 2, 18], t) is False


def test_saltos_reais_com_um_par_vizinho_ainda_passa():
    """Como no exemplo do usuario: pode ter 1 par vizinho (00:32, 00:38) desde que
    o resto salte pelo video."""
    t = _transcript(n=20)
    assert _montage_is_spread([1, 6, 7, 14, 19], t) is True  # so (6,7) sao vizinhos


# --- "gerando apenas 1 variacao": dedup de montagens quase iguais ------------

def test_montagens_quase_iguais_sao_deduplicadas():
    """Temperatura baixa gerava montagens quase identicas. As duas primeiras
    compartilham 3 de 4 segmentos (>60%) -> a segunda e descartada como clone."""
    t = _transcript(n=20)
    raw = {"montagens": [
        {"segments": [0, 7, 14, 19], "titulo": "original", "score": 90},
        {"segments": [0, 7, 14, 18], "titulo": "quase igual", "score": 88},  # 3/4 iguais
        {"segments": [2, 5, 11, 17], "titulo": "diferente", "score": 70},
    ]}
    montages, _rej = _resolve_montages(raw, t)

    titulos = [m["titulo"] for m in montages]
    assert "original" in titulos and "diferente" in titulos
    assert "quase igual" not in titulos, "montagem clone deveria ter sido deduplicada"


def test_segments_too_similar():
    assert _segments_too_similar([1, 2, 3, 4], [1, 2, 3, 9]) is True    # 3/4
    assert _segments_too_similar([1, 2, 3, 4], [1, 5, 6, 7]) is False   # 1/4


# --- "comendo palavras": padding adaptativo por trecho ----------------------

def test_trecho_da_montagem_estende_a_cauda_ate_o_silencio():
    """O `end` do Whisper e o fim do fonema. Antes, padding fixo de 0.08 decepava
    a ultima silaba. Agora a cauda se estende ate a proxima palavra (sem invadi-la)."""
    # seg 0: palavra termina em 1.0; proxima palavra so em 3.0 -> muita folga
    words = [
        Word(id=0, text="fim", start=0.5, end=1.0),
        Word(id=1, text="prox", start=3.0, end=3.5),
        Word(id=2, text="meio", start=6.0, end=6.5),
        Word(id=3, text="tres", start=9.0, end=9.5),
    ]
    segs = [Segment(id=i, start=w.start, end=w.end, text=w.text, word_ids=[i]) for i, w in enumerate(words)]
    t = TranscriptState(words=words, segments=segs)
    raw = {"montagens": [{"segments": [0, 2, 3], "titulo": "x", "score": 80}]}

    montages, _rej = _resolve_montages(raw, t)

    assert len(montages) == 1
    fim = montages[0]["pieces"][0]  # trecho do seg 0
    # cauda estendida alem de 1.0 (fim do fonema) -- nao mais o fixo 1.08
    assert fim["end"] > 1.08, "cauda nao foi estendida -- ainda comeria a silaba final"
    assert fim["end"] <= 1.0 + 0.25 + 1e-9, "cauda nao pode passar do teto de 0.25s"


# --- o bug reportado: "pedi 3, veio 1" -------------------------------------
# Causa raiz: o app pedia EXATAMENTE 3 candidatas pra IA; se 2 caissem no filtro
# de espalhamento, sobrava so 1 sem chance de reposicao. extract_montages agora
# pede folga (MONTAGE_REQUEST_BUFFER) pra sobrar material apos o filtro.

def test_pede_folga_a_mais_do_que_o_solicitado(monkeypatch):
    t = _transcript(n=20)
    pedidos = {}

    async def fake_call(_t, request_count, _min, _max):
        pedidos["request_count"] = request_count
        return {"montagens": []}

    monkeypatch.setattr("core.objectives._call_openai_montage", fake_call)
    asyncio.run(extract_montages(t, max_montages=3))

    assert pedidos["request_count"] == 3 + MONTAGE_REQUEST_BUFFER


def test_pedido_3_com_2_rejeitadas_ainda_entrega_3_gracas_a_folga(monkeypatch):
    """Reproduz o relato do usuario, mas com a folga: a IA propoe 5 (3 pedidas +
    2 de folga), 2 ficam agrupadas e sao descartadas, mas ainda sobram 3 boas."""
    t = _transcript(n=20)

    async def fake_call(_t, request_count, _min, _max):
        assert request_count == 5  # 3 + buffer(2)
        return {"montagens": [
            {"segments": [1, 2, 3], "titulo": "agrupada 1", "score": 95},   # rejeitada
            {"segments": [4, 5, 6], "titulo": "agrupada 2", "score": 90},   # rejeitada
            {"segments": [0, 8, 15], "titulo": "boa 1", "score": 85},
            {"segments": [1, 9, 16], "titulo": "boa 2", "score": 80},
            {"segments": [2, 10, 17], "titulo": "boa 3", "score": 75},
        ]}

    monkeypatch.setattr("core.objectives._call_openai_montage", fake_call)
    montages, meta = asyncio.run(extract_montages(t, max_montages=3))

    assert len(montages) == 3, "mesmo com 2 rejeitadas, a folga deveria manter as 3 pedidas"
    assert meta == {"requested": 3, "suggested": 5, "valid": 3, "delivered": 3, "descartadas_agrupadas": 2}


def test_meta_mostra_quando_entrega_menos_que_o_pedido(monkeypatch):
    """Se mesmo com folga a IA nao acha material espalhado o bastante, o app
    tem que EXPLICAR (via meta), nao so entregar menos em silencio."""
    t = _transcript(n=20)

    async def fake_call(_t, _req, _min, _max):
        return {"montagens": [{"segments": [1, 2, 3], "titulo": "so uma agrupada", "score": 90}]}

    monkeypatch.setattr("core.objectives._call_openai_montage", fake_call)
    montages, meta = asyncio.run(extract_montages(t, max_montages=3))

    assert montages == []
    assert meta["requested"] == 3
    assert meta["suggested"] == 1
    assert meta["descartadas_agrupadas"] == 1
