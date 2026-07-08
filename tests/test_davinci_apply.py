"""
Teste de orquestracao do apply_cuts do DaVinci usando fakes da API do Resolve
(nao ha Resolve rodando no CI/dev). Verifica o que o mapeamento puro nao cobre:

 - os campos internos (_color/_clip_id) NUNCA sao enviados ao Resolve
   (seriam recusados pela API — bug real se vazassem);
 - SetClipColor e chamado com a cor certa em cada clip novo;
 - a verificacao pos-aplicacao conta os clips da nova timeline.
"""
from types import SimpleNamespace

import pytest

import core.adapters.davinci as dv


class FakeTimelineItem:
    def __init__(self, start, end, left_offset, media, name="clip"):
        self._start, self._end, self._lo = start, end, left_offset
        self._media, self._name = media, name
        self.color_set_to = None

    def GetStart(self): return self._start
    def GetEnd(self): return self._end
    def GetLeftOffset(self): return self._lo
    def GetMediaPoolItem(self): return self._media
    def GetName(self): return self._name

    def SetClipColor(self, color):
        self.color_set_to = color
        return True


class FakeTimeline:
    def __init__(self, items, fps=30.0, start_frame=0, name="Original"):
        self._items, self._fps, self._start, self._name = items, fps, start_frame, name

    def GetSetting(self, key): return str(self._fps) if key == "timelineFrameRate" else ""
    def GetStartFrame(self): return self._start
    def GetName(self): return self._name
    def GetItemListInTrack(self, kind, idx): return self._items


class FakeMediaPool:
    def __init__(self, new_timeline):
        self._new = new_timeline
        self.received_clip_infos = None

    def CreateTimelineFromClips(self, name, clip_infos):
        self.received_clip_infos = clip_infos
        self._new._name = name
        return self._new


def _setup(monkeypatch, source_items, new_items):
    """Faz apply_cuts operar sobre timelines fake."""
    src_timeline = FakeTimeline(source_items)
    new_timeline = FakeTimeline(new_items, name="pendente")
    media_pool = FakeMediaPool(new_timeline)
    project = SimpleNamespace(
        GetCurrentTimeline=lambda: src_timeline,
        GetMediaPool=lambda: media_pool,
    )
    monkeypatch.setattr(dv, "_bootstrap", lambda: "FAKE_RESOLVE")
    monkeypatch.setattr(dv, "_current_project", lambda resolve: project)
    return media_pool, new_timeline


def _clip(start, end, cid="vir_000", color="Blue"):
    return SimpleNamespace(start=start, end=end, titulo="corte", id=cid, color=color)


def test_apply_strips_internal_fields_before_calling_resolve(monkeypatch):
    source = [FakeTimelineItem(0, 60000, 0, "MEDIA_A")]
    new_items = [FakeTimelineItem(0, 300, 0, "MEDIA_A")]
    media_pool, _ = _setup(monkeypatch, source, new_items)

    dv.apply_cuts([_clip(10.0, 20.0, color="Purple")], "Cortes Virais — X")

    sent = media_pool.received_clip_infos
    assert len(sent) == 1
    # os campos internos NAO podem ir para o Resolve
    assert "_color" not in sent[0]
    assert "_clip_id" not in sent[0]
    # os campos validos do clipInfo devem estar presentes
    assert set(sent[0].keys()) == {"mediaPoolItem", "startFrame", "endFrame"}


def test_apply_colors_each_new_clip(monkeypatch):
    source = [FakeTimelineItem(0, 60000, 0, "MEDIA_A")]
    new_items = [FakeTimelineItem(0, 300, 0, "MEDIA_A"), FakeTimelineItem(300, 600, 0, "MEDIA_A")]
    _setup(monkeypatch, source, new_items)

    result = dv.apply_cuts(
        [_clip(10.0, 20.0, cid="a", color="Purple"), _clip(30.0, 40.0, cid="b", color="Orange")],
        "Cortes Virais — X",
    )

    assert new_items[0].color_set_to == "Purple"
    assert new_items[1].color_set_to == "Orange"
    assert result["colored"] == 2
    assert result["applied"] == 2
    assert result["expected"] == 2
    assert result["new_timeline_name"] == "Cortes Virais — X"


def test_apply_warns_on_count_mismatch(monkeypatch):
    """CreateTimelineFromClips gerou menos clips que o esperado -> warning, nao crash."""
    source = [FakeTimelineItem(0, 60000, 0, "MEDIA_A")]
    new_items = [FakeTimelineItem(0, 300, 0, "MEDIA_A")]  # so 1, mas 2 cortes enviados
    _setup(monkeypatch, source, new_items)

    result = dv.apply_cuts(
        [_clip(10.0, 20.0, cid="a"), _clip(30.0, 40.0, cid="b")],
        "Cortes Virais — X",
    )

    assert result["expected"] == 2
    assert result["applied"] == 1
    assert any("esperava 2" in w for w in result["warnings"])


def test_apply_raises_when_no_clips_map(monkeypatch):
    source = [FakeTimelineItem(0, 300, 0, "MEDIA_A")]  # item curto
    _setup(monkeypatch, source, [])
    # corte em 100s cai muito alem do item -> nao mapeia
    with pytest.raises(RuntimeError, match="Nenhum corte pode ser mapeado"):
        dv.apply_cuts([_clip(100.0, 110.0)], "Cortes Virais — X")


def test_apply_raises_on_empty_clips(monkeypatch):
    with pytest.raises(RuntimeError, match="Nenhum corte aprovado"):
        dv.apply_cuts([], "Cortes Virais — X")
