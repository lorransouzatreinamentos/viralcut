"""
Testes da aritmetica de ticks do PremiereCutPlan. Analogo aos testes de frame
do DaVinci -- a parte perigosa (conversao tempo->tick com snap de frame) numa
funcao pura, testada sem Premiere.
"""
from types import SimpleNamespace

from core.adapters.premiere_plan import (
    TICKS_PER_SECOND,
    SeqItemDesc,
    build_cut_plan,
    seconds_to_frame_snapped_ticks,
)


def _clip(start, end, cid="vir_000", color="Blue", titulo="corte"):
    return SimpleNamespace(start=start, end=end, id=cid, color=color, titulo=titulo)


def test_ticks_per_second_constant():
    assert TICKS_PER_SECOND == 254016000000


def test_seconds_to_ticks_whole_second_30fps():
    # 1s exato -> 254016000000 ticks
    assert seconds_to_frame_snapped_ticks(1.0, 30.0) == "254016000000"


def test_seconds_to_ticks_snaps_to_frame():
    """1.02s @ 30fps: direcional. 'in' usa floor -> frame 30; 'out' usa ceil -> 31."""
    assert seconds_to_frame_snapped_ticks(1.02, 30.0, "in") == str(30 * (TICKS_PER_SECOND // 30))
    assert seconds_to_frame_snapped_ticks(1.02, 30.0, "out") == str(31 * (TICKS_PER_SECOND // 30))


def test_seconds_to_ticks_returns_string():
    """createSubClip exige ticks como STRING (ES3 sem inteiro 64-bit)."""
    result = seconds_to_frame_snapped_ticks(5.0, 25.0)
    assert isinstance(result, str)


def test_2997_fps_ticks_are_integer():
    """254016000000 divide 29.97 exatamente. Direcional: 'in' 1s -> floor(29.97)=29."""
    import math
    result = int(seconds_to_frame_snapped_ticks(1.0, 29.97, "in"))
    assert result == round(math.floor(1.0 * 29.97) * TICKS_PER_SECOND / 29.97)


def test_build_plan_simple_single_source():
    items = [SeqItemDesc(start=0.0, end=600.0, in_point=0.0, project_item_id="node_1")]
    clips = [_clip(10.0, 20.0, color="Purple")]

    plan = build_cut_plan(clips, items, fps=30.0, new_sequence_name="Cortes Virais — X")

    assert plan["new_sequence_name"] == "Cortes Virais — X"
    assert len(plan["cuts"]) == 1
    cut = plan["cuts"][0]
    assert cut["in_ticks"] == seconds_to_frame_snapped_ticks(10.0, 30.0)
    assert cut["out_ticks"] == seconds_to_frame_snapped_ticks(20.0, 30.0)
    assert cut["offset_sec"] == 0.0
    assert cut["label_index"] == 8  # Purple -> indice 8 (mapeamento verificado)
    assert cut["project_item_id"] == "node_1"


def test_build_plan_maps_source_inpoint():
    """Item aparado: in_point=100s. Corte no inicio da sequencia mapeia para 100s na origem."""
    items = [SeqItemDesc(start=0.0, end=600.0, in_point=100.0, project_item_id="n")]
    clips = [_clip(0.0, 5.0)]

    plan = build_cut_plan(clips, items, fps=30.0, new_sequence_name="X")

    assert plan["cuts"][0]["in_ticks"] == seconds_to_frame_snapped_ticks(100.0, 30.0)
    assert plan["cuts"][0]["out_ticks"] == seconds_to_frame_snapped_ticks(105.0, 30.0)


def test_build_plan_accumulates_offset():
    """Cortes encaixam sequencialmente na nova sequencia: offset soma as duracoes."""
    items = [SeqItemDesc(start=0.0, end=600.0, in_point=0.0, project_item_id="n")]
    clips = [_clip(10.0, 20.0, cid="a"), _clip(100.0, 130.0, cid="b")]

    plan = build_cut_plan(clips, items, fps=30.0, new_sequence_name="X")

    assert plan["cuts"][0]["offset_sec"] == 0.0
    assert plan["cuts"][1]["offset_sec"] == 10.0  # duracao do primeiro corte


def test_build_plan_skips_cut_in_gap():
    items = [SeqItemDesc(start=0.0, end=30.0, in_point=0.0, project_item_id="n")]
    clips = [_clip(100.0, 105.0, titulo="no vazio")]

    plan = build_cut_plan(clips, items, fps=30.0, new_sequence_name="X")

    assert plan["cuts"] == []
    assert any("no vazio" in w for w in plan["warnings"])


def test_build_plan_clamps_boundary_crossing():
    items = [SeqItemDesc(start=0.0, end=25.0, in_point=0.0, project_item_id="n")]
    clips = [_clip(20.0, 40.0, titulo="cruza")]

    plan = build_cut_plan(clips, items, fps=30.0, new_sequence_name="X")

    assert len(plan["cuts"]) == 1
    assert plan["cuts"][0]["out_ticks"] == seconds_to_frame_snapped_ticks(25.0, 30.0)
    assert any("grampeado" in w for w in plan["warnings"])


def test_build_plan_default_color_index():
    items = [SeqItemDesc(start=0.0, end=600.0, in_point=0.0, project_item_id="n")]
    clips = [_clip(1.0, 5.0, color="CorInexistente")]

    plan = build_cut_plan(clips, items, fps=30.0, new_sequence_name="X")

    assert plan["cuts"][0]["label_index"] == 2  # default Caribbean
