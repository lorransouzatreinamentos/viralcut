"""
Erro real do usuario: "Request too large for gpt-4o ... TPM: Limit 30000,
Requested 37853" ao clicar em extrair falas virais. A conta OpenAI dele tem um
limite de tokens/minuto baixo, e a transcricao inteira num unico prompt estourava.

Estes testes travam a correcao: cortes virais dividem a transcricao em LOTES
(cada corte e autocontido, nao precisa ver o video inteiro), e chamadas que
levam 429 (rate limit) sao retentadas com backoff antes de desistir.
"""
import asyncio

import httpx
import pytest

from core.model import Segment, TranscriptState, Word
from core.viral import (
    CHUNK_MAX_CHARS,
    _call_llm_batch_with_retry,
    _chunk_segments,
    extract_viral_clips,
)


def _segments(n: int, text_len: int = 40) -> list[Segment]:
    return [
        Segment(id=i, start=i * 5.0, end=i * 5.0 + 4.0, text="x" * text_len, word_ids=[i])
        for i in range(n)
    ]


def _transcript(n: int, text_len: int = 40) -> TranscriptState:
    segs = _segments(n, text_len)
    words = [Word(id=s.id, text=s.text, start=s.start, end=s.end) for s in segs]
    return TranscriptState(words=words, segments=[Segment(**{**s.model_dump(), "word_ids": [s.id]}) for s in segs])


# --- _chunk_segments (pure) --------------------------------------------------

def test_transcricao_curta_fica_num_unico_lote():
    segs = _segments(5, text_len=20)
    chunks = _chunk_segments(segs)
    assert len(chunks) == 1
    assert sum(len(c) for c in chunks) == 5


def test_transcricao_longa_e_dividida_em_varios_lotes():
    """Reproduz o cenario do erro: transcricao grande o suficiente pra estourar
    o orcamento de 1 lote. Cada segmento tem ~230 chars (~30000/130 ~ o cenario real)."""
    segs = _segments(200, text_len=200)
    chunks = _chunk_segments(segs)
    assert len(chunks) > 1, "transcricao grande deveria ter sido dividida"
    # nenhum segmento se perde e a ordem e preservada
    flat = [s.id for c in chunks for s in c]
    assert flat == [s.id for s in segs]


def test_cada_lote_respeita_o_orcamento_de_caracteres():
    segs = _segments(200, text_len=200)
    chunks = _chunk_segments(segs)
    for c in chunks[:-1]:  # o ultimo lote pode ficar menor, os do meio respeitam o teto
        total = sum(len(s.text) + 30 for s in c)
        assert total <= CHUNK_MAX_CHARS + 300  # +1 linha de folga (a que estourou o teto)


def test_um_segmento_gigante_sozinho_nao_trava():
    """Um segmento maior que o orcamento inteiro ainda vira 1 lote (nao quebra o texto)."""
    segs = [Segment(id=0, start=0, end=100, text="x" * 50000, word_ids=[0])]
    chunks = _chunk_segments(segs)
    assert len(chunks) == 1
    assert len(chunks[0]) == 1


# --- extract_viral_clips: merge de lotes -------------------------------------

def test_lotes_multiplos_sao_chamados_e_resultado_e_mesclado(monkeypatch):
    """A transcricao inteira vira varias chamadas de IA; os candidatos de todas
    sao juntados antes do filtro final -- como se fosse 1 chamada so."""
    t = _transcript(200, text_len=200)
    chamadas = []

    async def fake_batch(segments, min_score, max_clips, min_dur, max_dur):
        chamadas.append([s.id for s in segments])
        # cada lote propoe 1 corte usando o primeiro segmento do lote
        sid = segments[0].id
        return {"clips": [{"start_seg_id": sid, "end_seg_id": sid, "titulo": f"corte {sid}", "score": 80}]}

    monkeypatch.setattr("core.viral._call_llm_batch_with_retry", fake_batch)
    clips, _rejected = asyncio.run(extract_viral_clips(t, min_score=0, max_clips=50, min_dur=0, max_dur=0))

    assert len(chamadas) > 1, "deveria ter dividido em varios lotes"
    assert len(clips) == len(chamadas), "resultado deveria juntar os candidatos de TODOS os lotes"


def test_um_lote_falha_outros_ainda_entregam_resultado(monkeypatch):
    """Um lote com erro (ex: 429 apos esgotar os retries) nao derruba os demais --
    resultado parcial e sempre melhor que erro total."""
    t = _transcript(200, text_len=200)
    call_count = {"n": 0}

    async def flaky_batch(segments, min_score, max_clips, min_dur, max_dur):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simula falha permanente neste lote")
        sid = segments[0].id
        return {"clips": [{"start_seg_id": sid, "end_seg_id": sid, "titulo": "ok", "score": 80}]}

    monkeypatch.setattr("core.viral._call_llm_batch_with_retry", flaky_batch)
    clips, _rejected = asyncio.run(extract_viral_clips(t, min_score=0, max_clips=50, min_dur=0, max_dur=0))

    assert call_count["n"] > 1
    assert len(clips) == call_count["n"] - 1, "deveria ter entregue os lotes que funcionaram"


def test_todos_os_lotes_falham_da_erro_explicativo(monkeypatch):
    t = _transcript(200, text_len=200)

    async def always_fails(segments, min_score, max_clips, min_dur, max_dur):
        raise RuntimeError("falha")

    monkeypatch.setattr("core.viral._call_llm_batch_with_retry", always_fails)
    with pytest.raises(RuntimeError, match="Tente novamente em 1 minuto"):
        asyncio.run(extract_viral_clips(t, min_score=0, max_clips=50, min_dur=0, max_dur=0))


# --- retry com backoff em 429 -------------------------------------------------

def _rate_limit_error() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(429, request=req, json={"error": {"message": "rate limited"}})
    return httpx.HTTPStatusError("rate limited", request=req, response=resp)


def test_retry_tenta_de_novo_apos_429_e_da_certo(monkeypatch):
    """1o e 2o tentativa levam 429 (rate limit), 3a funciona -- nao deveria falhar."""
    tentativas = {"n": 0}

    async def fake_call_openai(segments, min_score, max_clips, min_dur, max_dur):
        tentativas["n"] += 1
        if tentativas["n"] < 3:
            raise _rate_limit_error()
        return {"clips": []}

    async def no_sleep(_seconds):
        pass  # nao esperar de verdade no teste

    monkeypatch.setattr("core.viral._call_openai", fake_call_openai)
    monkeypatch.setattr("core.viral.settings.llm_provider", "openai")
    monkeypatch.setattr("core.viral._sleep", no_sleep)

    result = asyncio.run(_call_llm_batch_with_retry(_segments(3), 0, 10, 0, 0))

    assert tentativas["n"] == 3
    assert result == {"clips": []}


def test_retry_desiste_apos_3_tentativas_com_429(monkeypatch):
    async def sempre_429(segments, min_score, max_clips, min_dur, max_dur):
        raise _rate_limit_error()

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr("core.viral._call_openai", sempre_429)
    monkeypatch.setattr("core.viral.settings.llm_provider", "openai")
    monkeypatch.setattr("core.viral._sleep", no_sleep)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_call_llm_batch_with_retry(_segments(3), 0, 10, 0, 0))


def test_retry_nao_reentrenta_erro_que_nao_e_429(monkeypatch):
    """Erro que nao e rate limit (ex: chave invalida) falha na hora -- retry nao ajudaria."""
    tentativas = {"n": 0}

    async def erro_generico(segments, min_score, max_clips, min_dur, max_dur):
        tentativas["n"] += 1
        raise RuntimeError("OPENAI_API_KEY nao configurada")

    monkeypatch.setattr("core.viral._call_openai", erro_generico)
    monkeypatch.setattr("core.viral.settings.llm_provider", "openai")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        asyncio.run(_call_llm_batch_with_retry(_segments(3), 0, 10, 0, 0))
    assert tentativas["n"] == 1, "erro que nao e 429 nao deveria ser retentado"
