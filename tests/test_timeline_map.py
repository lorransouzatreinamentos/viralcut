"""
Bug reportado pelo usuario: timeline com 5 videos, o app so analisou o primeiro.
Causa: transcrevia UM arquivo-fonte (o clipe mais longo). Estes testes travam a
correcao -- costurar as transcricoes dos varios videos numa unica em tempo de
timeline, e dividir cada corte de volta nos clipes que ele atravessa.
"""
from core.model import Segment, TranscriptState, Word
from core.timeline_map import TimelineClip, remap_to_timeline, split_cut


def _t(words):
    """words: [(id, text, start, end)] -> TranscriptState com 1 segmento por palavra."""
    ws = [Word(id=i, text=t, start=s, end=e) for (i, t, s, e) in words]
    segs = [Segment(id=w.id, start=w.start, end=w.end, text=w.text, word_ids=[w.id]) for w in ws]
    return TranscriptState(words=ws, segments=segs)


# --- remap: a transcricao passa a cobrir TODOS os videos ---------------------

def test_dois_videos_viram_uma_transcricao_continua():
    """O caso do usuario em miniatura: 2 videos na timeline, fala nos dois."""
    vid_a = _t([(0, "ola", 1.0, 1.5), (1, "mundo", 1.5, 2.0)])
    vid_b = _t([(0, "tudo", 0.5, 1.0), (1, "bem", 1.0, 1.5)])
    clips = [
        # video A: usa 1.0-2.0s da origem, aparece em 0-1s da timeline
        TimelineClip(source_key="a.mov", ref="a.mov", src_in=1.0, tl_start=0.0, tl_end=1.0),
        # video B: usa 0.5-1.5s da origem, aparece em 1-2s da timeline
        TimelineClip(source_key="b.mov", ref="b.mov", src_in=0.5, tl_start=1.0, tl_end=2.0),
    ]
    out = remap_to_timeline(clips, {"a.mov": vid_a, "b.mov": vid_b})

    assert [w.text for w in out.words] == ["ola", "mundo", "tudo", "bem"], "perdeu fala de algum video"
    # video A: 1.0s origem -> 0.0s timeline (shift = -1.0)
    assert abs(out.words[0].start - 0.0) < 1e-6
    # video B: 0.5s origem -> 1.0s timeline (shift = +0.5); "bem" 1.0-1.5 -> 1.5-2.0
    assert abs(out.words[2].start - 1.0) < 1e-6
    assert abs(out.words[3].end - 2.0) < 1e-6
    # ids contiguos e unicos (o modelo exige)
    assert [w.id for w in out.words] == [0, 1, 2, 3]


def test_cinco_videos_todos_aparecem():
    """Exatamente o cenario relatado: 5 videos, cada um com uma fala."""
    clips, transcripts = [], {}
    for i in range(5):
        key = f"v{i}.mov"
        transcripts[key] = _t([(0, f"fala{i}", 0.0, 0.5)])
        clips.append(TimelineClip(source_key=key, ref=key, src_in=0.0,
                                  tl_start=float(i), tl_end=float(i) + 0.5))
    out = remap_to_timeline(clips, transcripts)
    assert [w.text for w in out.words] == ["fala0", "fala1", "fala2", "fala3", "fala4"]


def test_ordem_da_timeline_manda_nao_a_ordem_da_lista():
    a = _t([(0, "primeiro", 0.0, 0.5)])
    b = _t([(0, "segundo", 0.0, 0.5)])
    # passo B antes de A na lista, mas A vem antes na timeline
    clips = [
        TimelineClip(source_key="b", ref="b", src_in=0.0, tl_start=5.0, tl_end=5.5),
        TimelineClip(source_key="a", ref="a", src_in=0.0, tl_start=0.0, tl_end=0.5),
    ]
    out = remap_to_timeline(clips, {"a": a, "b": b})
    assert [w.text for w in out.words] == ["primeiro", "segundo"]


def test_apara_fala_fora_do_trecho_usado():
    """Se o clipe usa so parte do arquivo, fala fora do in/out nao entra."""
    vid = _t([(0, "antes", 0.0, 0.5), (1, "usado", 2.0, 2.5), (2, "depois", 5.0, 5.5)])
    clips = [TimelineClip(source_key="v", ref="v", src_in=1.8, tl_start=0.0, tl_end=1.0)]  # 1.8-2.8s
    out = remap_to_timeline(clips, {"v": vid})
    assert [w.text for w in out.words] == ["usado"], "nao filtrou fala fora do trecho do clipe"


def test_mesmo_arquivo_em_dois_clipes():
    """Mesmo video cortado em 2 pedacos na timeline: cada pedaco pega sua fala."""
    vid = _t([(0, "inicio", 0.2, 0.7), (1, "fim", 5.2, 5.7)])
    clips = [
        TimelineClip(source_key="v", ref="v", src_in=0.0, tl_start=0.0, tl_end=1.0),   # 0-1s origem
        TimelineClip(source_key="v", ref="v", src_in=5.0, tl_start=1.0, tl_end=2.0),   # 5-6s origem
    ]
    out = remap_to_timeline(clips, {"v": vid})
    assert [w.text for w in out.words] == ["inicio", "fim"]
    assert abs(out.words[1].start - 1.2) < 1e-6  # 5.2 origem -> 1.2 timeline


# --- split: corte de volta para as fontes ------------------------------------

def _clips_2():
    return [
        TimelineClip(source_key="a", ref="a", src_in=10.0, tl_start=0.0, tl_end=4.0),
        TimelineClip(source_key="b", ref="b", src_in=0.0, tl_start=4.0, tl_end=8.0),
    ]


def test_corte_dentro_de_um_clipe_vira_um_span():
    spans = split_cut(1.0, 3.0, _clips_2())
    assert len(spans) == 1
    assert spans[0].ref == "a"
    assert spans[0].src_start == 11.0 and spans[0].src_end == 13.0  # +src_in 10


def test_corte_que_cruza_fronteira_vira_dois_spans():
    """Isto e o que faltava: cortar/limpar silencio atravessando videos."""
    spans = split_cut(2.0, 6.0, _clips_2())
    assert len(spans) == 2
    assert spans[0].ref == "a" and spans[0].src_start == 12.0 and spans[0].src_end == 14.0
    assert spans[1].ref == "b" and spans[1].src_start == 0.0 and spans[1].src_end == 2.0


def test_corte_cobrindo_toda_a_timeline():
    spans = split_cut(0.0, 8.0, _clips_2())
    assert [s.ref for s in spans] == ["a", "b"]
    assert spans[0].src_start == 10.0 and spans[0].src_end == 14.0
    assert spans[1].src_start == 0.0 and spans[1].src_end == 4.0


def test_gap_entre_clipes_nao_gera_span():
    clips = [
        TimelineClip(source_key="a", ref="a", src_in=0.0, tl_start=0.0, tl_end=2.0),
        TimelineClip(source_key="b", ref="b", src_in=0.0, tl_start=5.0, tl_end=7.0),  # gap 2-5s
    ]
    spans = split_cut(2.5, 4.5, clips)  # cai inteiro no gap
    assert spans == []


def test_spans_saem_em_ordem_de_timeline():
    spans = split_cut(0.0, 8.0, list(reversed(_clips_2())))
    assert [s.tl_start for s in spans] == sorted(s.tl_start for s in spans)
