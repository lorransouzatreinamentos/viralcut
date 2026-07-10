"""
Testes da aritmetica tempo->frame do adapter DaVinci (build_clip_infos).

Esta e a parte mais perigosa da Fase 3: um erro de 1 frame aqui e silencioso
e so aparece quando um humano revisa o corte. Por isso a matematica esta numa
funcao PURA, testada aqui sem precisar do Resolve rodando.

Casos cobertos:
 - timeline comecando em frame 0 (caso simples)
 - timeline comecando em 01:00:00:00 (GetStartFrame != 0)
 - clip de origem aparado (left_offset != 0)
 - corte num gap (sem item) -> pulado
 - corte cruzando fronteira de clip -> grampeado
 - item sem midia -> pulado
 - fps 29.97 (drop-frame nao quebra aritmetica de frame inteiro)
"""
from types import SimpleNamespace

from core.adapters.davinci import TimelineItemDesc, build_clip_infos


def _clip(start, end, titulo="corte", cid="vir_000", color="Blue"):
    return SimpleNamespace(start=start, end=end, titulo=titulo, id=cid, color=color)


def test_simple_timeline_starting_at_zero():
    """Timeline em frame 0, clip com left_offset 0 -> frame de origem == frame de audio."""
    items = [TimelineItemDesc(start=0, end=30000, left_offset=0, media_pool_item="MEDIA_A")]
    clips = [_clip(10.0, 20.0)]  # 10s-20s @ 30fps -> frames 300-600

    infos, warnings = build_clip_infos(clips, items, timeline_start_frame=0, fps=30.0)

    assert warnings == []
    assert len(infos) == 1
    assert infos[0]["startFrame"] == 300
    assert infos[0]["endFrame"] == 600
    assert infos[0]["mediaPoolItem"] == "MEDIA_A"


def test_timeline_starting_at_one_hour():
    """GetStartFrame = 90000 (01:00:00:00 @ 25fps). audio t=0 corresponde a esse frame."""
    tl_start = 90000
    items = [TimelineItemDesc(start=90000, end=120000, left_offset=0, media_pool_item="M")]
    clips = [_clip(0.0, 4.0)]  # primeiros 4s @ 25fps -> 100 frames de origem

    infos, warnings = build_clip_infos(clips, items, timeline_start_frame=tl_start, fps=25.0)

    assert warnings == []
    # corte comeca no frame 0 da MIDIA (left_offset 0 + (90000 - 90000))
    assert infos[0]["startFrame"] == 0
    assert infos[0]["endFrame"] == 100


def test_trimmed_source_clip_uses_left_offset():
    """Clip aparado: os primeiros 500 frames da origem foram cortados fora na timeline.
    Um corte no inicio da timeline deve mapear para frame 500 da origem, nao 0."""
    items = [TimelineItemDesc(start=0, end=30000, left_offset=500, media_pool_item="M")]
    clips = [_clip(0.0, 2.0)]  # 2s @ 30fps = 60 frames

    infos, warnings = build_clip_infos(clips, items, timeline_start_frame=0, fps=30.0)

    assert warnings == []
    assert infos[0]["startFrame"] == 500          # left_offset + (0 - 0)
    assert infos[0]["endFrame"] == 560            # 500 + 60


def test_cut_in_gap_is_skipped_with_warning():
    items = [TimelineItemDesc(start=0, end=300, left_offset=0, media_pool_item="M")]
    clips = [_clip(100.0, 105.0, titulo="no vazio")]  # frame 3000, alem do fim do item (300)

    infos, warnings = build_clip_infos(clips, items, timeline_start_frame=0, fps=30.0)

    assert infos == []
    assert len(warnings) == 1
    assert "no vazio" in warnings[0]


def test_cut_past_end_of_only_clip_truncates():
    """Item unico vai ate frame 600. Corte 15s-25s (450-750) sem midia depois:
    o span so cobre 450-600 (nao ha o que cortar depois). Sem warning de grampe."""
    items = [TimelineItemDesc(start=0, end=600, left_offset=0, media_pool_item="M")]
    clips = [_clip(15.0, 25.0, titulo="passa do fim")]

    infos, warnings = build_clip_infos(clips, items, timeline_start_frame=0, fps=30.0)

    assert len(infos) == 1
    assert infos[0]["startFrame"] == 450
    assert infos[0]["endFrame"] == 600


def test_cut_crossing_two_videos_splits_into_two():
    """O bug reportado: corte que cruza a edicao entre 2 videos vira 2 spans,
    cada um na sua fonte, ambos com a cor/id do corte."""
    items = [
        TimelineItemDesc(start=0, end=600, left_offset=0, media_pool_item="VIDEO_A"),
        TimelineItemDesc(start=600, end=1200, left_offset=100, media_pool_item="VIDEO_B"),
    ]
    clips = [_clip(15.0, 25.0, titulo="cruza", cid="x", color="Pink")]  # 450-750 na timeline

    infos, warnings = build_clip_infos(clips, items, timeline_start_frame=0, fps=30.0)

    assert len(infos) == 2, "corte que cruza fronteira deveria virar 2 spans"
    # span 1: video A, timeline 450-600 -> origem 450-600 (left_offset 0)
    assert infos[0]["mediaPoolItem"] == "VIDEO_A"
    assert infos[0]["startFrame"] == 450 and infos[0]["endFrame"] == 600
    # span 2: video B, timeline 600-750 -> origem 100+(600-600)=100 ate 100+150=250
    assert infos[1]["mediaPoolItem"] == "VIDEO_B"
    assert infos[1]["startFrame"] == 100 and infos[1]["endFrame"] == 250
    # os dois pedacos herdam a mesma cor e id do corte
    assert infos[0]["_color"] == infos[1]["_color"] == "Pink"
    assert infos[0]["_clip_id"] == infos[1]["_clip_id"] == "x"


def test_item_without_media_is_skipped():
    items = [TimelineItemDesc(start=0, end=30000, left_offset=0, media_pool_item=None)]
    clips = [_clip(1.0, 5.0, titulo="sem midia")]

    infos, warnings = build_clip_infos(clips, items, timeline_start_frame=0, fps=30.0)

    assert infos == []
    assert any("sem midia" in w for w in warnings)


def test_2997_fps_frame_math():
    """29.97 nao quebra: frames inteiros com arredondamento DIRECIONAL (floor no
    inicio, ceil no fim) -- o corte nunca encolhe."""
    import math
    items = [TimelineItemDesc(start=0, end=100000, left_offset=0, media_pool_item="M")]
    clips = [_clip(10.0, 20.0)]

    infos, warnings = build_clip_infos(clips, items, timeline_start_frame=0, fps=29.97)

    assert warnings == []
    assert infos[0]["startFrame"] == math.floor(10.0 * 29.97)  # 299
    assert infos[0]["endFrame"] == math.ceil(20.0 * 29.97)     # 600


def test_multiple_clips_mapped_independently():
    items = [TimelineItemDesc(start=0, end=60000, left_offset=0, media_pool_item="M")]
    clips = [_clip(5.0, 10.0, cid="a"), _clip(100.0, 110.0, cid="b")]

    infos, warnings = build_clip_infos(clips, items, timeline_start_frame=0, fps=30.0)

    assert warnings == []
    assert len(infos) == 2
    assert infos[0]["_clip_id"] == "a"
    assert infos[1]["_clip_id"] == "b"
    assert infos[1]["startFrame"] == 3000
