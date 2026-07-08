"""
Cache de transcricao (pedido do usuario): "se ja tiver uma transcricao pra
determinado video e o app constatar que for igual ele usa a em cache, mas dando
ao usuario liberdade de transcrever novamente".

E a garantia de que a transcricao NUNCA cai escondida pra nuvem.
"""
import asyncio

import pytest

from core import cache, transcribe
from core.model import Segment, TranscriptState, Word


@pytest.fixture(autouse=True)
def cache_isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "entrevista.mov"
    p.write_bytes(b"conteudo do video")
    return str(p)


def _fake_transcript():
    return TranscriptState(
        words=[Word(id=0, text="oi", start=0.0, end=0.5)],
        segments=[Segment(id=0, start=0.0, end=0.5, text="oi", word_ids=[0])],
    )


def _run(coro):
    return asyncio.run(coro)


def _patch_local(monkeypatch, counter):
    def fake(_path, _lang):
        counter["n"] += 1
        return _fake_transcript()
    monkeypatch.setattr(transcribe, "_local_transcribe_sync", fake)


# --- fingerprint -------------------------------------------------------------

def test_fingerprint_muda_quando_o_arquivo_muda(video):
    antes = cache.fingerprint(video)
    with open(video, "wb") as f:
        f.write(b"outro conteudo, outro tamanho ainda")
    assert cache.fingerprint(video) != antes


def test_fingerprint_none_para_arquivo_inexistente():
    assert cache.fingerprint("/nao/existe.mov") is None


def test_fingerprint_separa_idiomas(video):
    assert cache.fingerprint(video, "pt") != cache.fingerprint(video, "en")


# --- comportamento do orquestrador -------------------------------------------

def test_segunda_chamada_usa_cache_e_nao_transcreve_de_novo(monkeypatch, video, tmp_path):
    n = {"n": 0}
    _patch_local(monkeypatch, n)

    _t1, m1 = _run(transcribe.transcribe_timeline_audio(video, str(tmp_path)))
    t2, m2 = _run(transcribe.transcribe_timeline_audio(video, str(tmp_path)))

    assert n["n"] == 1, "transcreveu duas vezes o mesmo arquivo intacto"
    assert m1["cached"] is False and m2["cached"] is True
    assert t2.segments[0].text == "oi", "cache devolveu transcricao corrompida"


def test_force_ignora_o_cache(monkeypatch, video, tmp_path):
    """O botao 'Transcrever novamente'."""
    n = {"n": 0}
    _patch_local(monkeypatch, n)

    _run(transcribe.transcribe_timeline_audio(video, str(tmp_path)))
    _t, meta = _run(transcribe.transcribe_timeline_audio(video, str(tmp_path), force=True))

    assert n["n"] == 2
    assert meta["cached"] is False


def test_arquivo_modificado_invalida_o_cache(monkeypatch, video, tmp_path):
    n = {"n": 0}
    _patch_local(monkeypatch, n)

    _run(transcribe.transcribe_timeline_audio(video, str(tmp_path)))
    with open(video, "wb") as f:
        f.write(b"o usuario trocou o video de origem por outro maior")
    _run(transcribe.transcribe_timeline_audio(video, str(tmp_path)))

    assert n["n"] == 2, "reusou transcricao de um video que mudou"


def test_cache_key_path_separa_do_audio_temporario(monkeypatch, video, tmp_path):
    """O wav temporario muda de nome a cada job; a chave e o video de origem."""
    n = {"n": 0}
    _patch_local(monkeypatch, n)

    wav1, wav2 = tmp_path / "job1.wav", tmp_path / "job2.wav"
    for w in (wav1, wav2):
        w.write_bytes(b"audio")

    _run(transcribe.transcribe_timeline_audio(str(wav1), str(tmp_path), cache_key_path=video))
    _t, meta = _run(transcribe.transcribe_timeline_audio(str(wav2), str(tmp_path), cache_key_path=video))

    assert n["n"] == 1
    assert meta["cached"] is True


# --- sempre local ------------------------------------------------------------

def test_sem_faster_whisper_falha_com_instrucao_em_vez_de_ir_pra_nuvem(monkeypatch, video, tmp_path):
    """Regra do usuario: 'a transcricao sempre vai ser feita pelo local nao via IA'.
    Sem a engine local, o app tem que ERRAR -- nunca gastar API sem avisar."""
    monkeypatch.setattr(transcribe, "_LOCAL_SCRIPT", tmp_path / "nao_existe.py")

    def nunca(*_a, **_k):
        raise AssertionError("chamou a API da nuvem sem o usuario pedir")
    monkeypatch.setattr(transcribe, "_transcribe_via_api", nunca)

    with pytest.raises(RuntimeError, match="pip install faster-whisper"):
        _run(transcribe.transcribe_timeline_audio(video, str(tmp_path)))


def test_engine_padrao_e_local():
    from core.config import Settings
    assert Settings.transcribe_engine in ("local", "api")
    import os
    if not os.getenv("VIRALCUT_TRANSCRIBE"):
        assert Settings.transcribe_engine == "local"


def test_falha_local_nao_grava_cache(monkeypatch, video, tmp_path):
    monkeypatch.setattr(transcribe, "_LOCAL_SCRIPT", tmp_path / "nao_existe.py")
    with pytest.raises(RuntimeError):
        _run(transcribe.transcribe_timeline_audio(video, str(tmp_path)))
    assert cache.load(video) is None
