"""
Cenario reportado pelo usuario: no DaVinci ele selecionou um CLIPE no Media Pool
em vez de abrir uma timeline, e recebeu um erro sem instrucao do que fazer.

Estes testes travam as mensagens de erro acionaveis (dizem o que fazer) de
list_timeline_clips -- a funcao que le TODOS os clipes da timeline aberta.
"""
from types import SimpleNamespace

import pytest

import core.adapters.davinci as dv


class FakeItem:
    def __init__(self, mpi="M", start=0, end=300, left=0, name="clip"):
        self._mpi, self._start, self._end, self._left, self._name = mpi, start, end, left, name

    def GetMediaPoolItem(self): return self._mpi
    def GetStart(self): return self._start
    def GetEnd(self): return self._end
    def GetLeftOffset(self): return self._left
    def GetName(self): return self._name


class FakeTimeline:
    def __init__(self, name="Timeline 1", items=None):
        self._name, self._items = name, items or []

    def GetName(self): return self._name
    def GetTrackCount(self, _kind): return 1
    def GetItemListInTrack(self, _kind, _i): return self._items
    def GetSetting(self, _k): return "24"
    def GetStartFrame(self): return 0


def _project(current_timeline=None, timelines=()):
    return SimpleNamespace(
        GetCurrentTimeline=lambda: current_timeline,
        GetTimelineCount=lambda: len(timelines),
        GetTimelineByIndex=lambda i: timelines[i - 1],  # API e 1-based
    )


def _patch(monkeypatch, project):
    monkeypatch.setattr(dv, "_bootstrap", lambda: "RESOLVE")
    monkeypatch.setattr(dv, "_current_project", lambda _r: project)


def test_sem_timeline_aberta_lista_as_existentes(monkeypatch):
    """Cenario do Rhayan: clicou num video, nao abriu timeline.
    A mensagem tem que dizer isso E mostrar quais timelines existem."""
    tls = [FakeTimeline("Entrevista FINAL"), FakeTimeline("Rascunho")]
    _patch(monkeypatch, _project(current_timeline=None, timelines=tls))

    with pytest.raises(RuntimeError) as e:
        dv.list_timeline_clips()

    msg = str(e.value)
    assert "Media Pool" in msg, "nao explica que selecionar clipe nao basta"
    assert "Entrevista FINAL" in msg, "nao lista as timelines disponiveis"
    assert "Edit" in msg, "nao diz onde abrir a timeline"


def test_projeto_sem_nenhuma_timeline(monkeypatch):
    _patch(monkeypatch, _project(current_timeline=None, timelines=[]))

    with pytest.raises(RuntimeError, match="nao tem nenhuma timeline"):
        dv.list_timeline_clips()


def test_timeline_aberta_mas_vazia_diz_o_nome_dela(monkeypatch):
    tl = FakeTimeline("Timeline Vazia", items=[])
    _patch(monkeypatch, _project(current_timeline=tl))

    with pytest.raises(RuntimeError) as e:
        dv.list_timeline_clips()

    assert "Timeline Vazia" in str(e.value), "nao diz qual timeline esta aberta"


def test_timeline_com_varios_videos_lista_todos(monkeypatch):
    """O coracao do fix: 3 videos na timeline -> 3 clipes, nao 1."""
    def mpi(path):
        return SimpleNamespace(GetClipProperty=lambda k=None: {"File Path": path, "FPS": "24"})
    items = [
        FakeItem(mpi=mpi("/v/a.mov"), start=0, end=240, left=0),
        FakeItem(mpi=mpi("/v/b.mov"), start=240, end=480, left=48),
        FakeItem(mpi=mpi("/v/c.mov"), start=480, end=720, left=0),
    ]
    _patch(monkeypatch, _project(current_timeline=FakeTimeline("Multi", items=items)))

    info = dv.list_timeline_clips()

    assert len(info["clips"]) == 3, "nao leu todos os videos da timeline"
    assert info["sources"] == ["/v/a.mov", "/v/b.mov", "/v/c.mov"]
    # posicoes em segundos (24 fps): clipe 2 comeca em 240/24 = 10s
    assert abs(info["clips"][1]["tl_start"] - 10.0) < 1e-6
    # in-point do clipe 2: left 48 / 24 = 2s
    assert abs(info["clips"][1]["src_in"] - 2.0) < 1e-6
    assert abs(info["duration_sec"] - 30.0) < 1e-6  # 720/24


def test_listar_timelines_e_1_based_e_nao_quebra(monkeypatch):
    tls = [FakeTimeline("A"), FakeTimeline("B"), FakeTimeline("C")]
    assert dv.list_timelines(_project(timelines=tls)) == ["A", "B", "C"]


def test_listar_timelines_tolera_api_indisponivel():
    """Nunca derruba o fluxo por causa da listagem (e so ajuda de mensagem)."""
    quebrado = SimpleNamespace(
        GetTimelineCount=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert dv.list_timelines(quebrado) == []
