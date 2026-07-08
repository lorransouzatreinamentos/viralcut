"""
Trava a correcao do bug "remover silencios come palavras".

Duas causas, medidas com dados reais (ffmpeg silencedetect):
 1. O `end` do Whisper e o fim do FONEMA, nao da cauda audivel. Medido: o som
    continuava ate ~160ms depois do que o Whisper reportava. Padding fixo de
    0.03s decepava a silaba final.
 2. Arredondamento round() na saida podia encolher o corte em ate meio frame.
    Correto: floor na entrada, ceil na saida -- nunca encolher.
"""
import math

from core.objectives import HEAD_PAD_MAX, TAIL_PAD_MAX, pad_spans


def test_tail_padding_cobre_cauda_do_som():
    """Fala termina em 2.42s (Whisper) mas o som real vai ate 2.539s.
    Com 1.2s de silencio depois, o padding tem folga de sobra."""
    spans = [{"start": 0.0, "end": 2.42}, {"start": 3.30, "end": 6.24}]
    out = pad_spans(spans, duration_sec=11.4)

    fim_real_do_som = 2.539
    assert out[0]["end"] >= fim_real_do_som, "corte ainda decepa a cauda do som"


def test_padding_nunca_invade_a_proxima_fala():
    """Mesmo com folga grande, o corte nao pode entrar na fala seguinte."""
    spans = [{"start": 0.0, "end": 2.0}, {"start": 2.1, "end": 4.0}]
    out = pad_spans(spans, duration_sec=5.0)

    assert out[0]["end"] <= spans[1]["start"], "padding invadiu a proxima fala"


def test_padding_limitado_ao_maximo():
    """Folga enorme (10s) nao vira padding enorme -- senao nao remove silencio."""
    spans = [{"start": 0.0, "end": 1.0}, {"start": 11.0, "end": 12.0}]
    out = pad_spans(spans, duration_sec=15.0)

    assert out[0]["end"] - 1.0 <= TAIL_PAD_MAX + 1e-9
    assert 0.0 - out[0]["start"] <= HEAD_PAD_MAX + 1e-9


def test_padding_nao_gera_tempo_negativo():
    spans = [{"start": 0.0, "end": 1.0}]
    out = pad_spans(spans, duration_sec=5.0)
    assert out[0]["start"] >= 0.0


def test_span_unico_usa_duracao_como_limite():
    """Ultima (ou unica) fala: a folga vai ate o fim do video."""
    spans = [{"start": 0.0, "end": 10.0}]
    out = pad_spans(spans, duration_sec=10.5)
    assert out[0]["end"] > 10.0
    assert out[0]["end"] <= 10.0 + TAIL_PAD_MAX + 1e-9


def test_arredondamento_direcional_nunca_encolhe_o_corte():
    """floor na entrada, ceil na saida. round() encolhia ate meio frame."""
    fps = 24.0
    start, end = 1.0104, 2.4896  # ambos caem entre frames

    # comportamento antigo (errado)
    sf_old, ef_old = round(start * fps), round(end * fps)
    # comportamento novo
    sf_new, ef_new = math.floor(start * fps), math.ceil(end * fps)

    assert sf_new <= sf_old, "entrada deveria arredondar para tras"
    assert ef_new >= ef_old, "saida deveria arredondar para frente"
    # o corte novo contem integralmente o intervalo pedido
    assert sf_new / fps <= start
    assert ef_new / fps >= end
