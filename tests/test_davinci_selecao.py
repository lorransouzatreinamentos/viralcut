"""
Cenario reportado pelo usuario: no DaVinci ele selecionou um CLIPE no Media Pool
em vez de abrir uma timeline, e recebeu um erro sem instrucao do que fazer.

Estes testes travam as mensagens de erro acionaveis (dizem o que fazer) e o
retorno separado de timeline_name vs name (nome do clipe), que era a origem da
confusao: o botao dizia "selecionar sequencia" mas exibia o nome do video.
"""
from types import SimpleNamespace

import pytest

import core.adapters.davinci as dv


class FakeTimeline:
    def __init__(self, name="Timeline 1", items=None):
        self._name, self._items = name, items or []

    def GetName(self): return self._name
    def GetTrackCount(self, _kind): return 1
    def GetItemListInTrack(self, _kind, _i): return self._items
    def GetSetting(self, _k): return "24"


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
        dv._get_main_source()

    msg = str(e.value)
    assert "Media Pool" in msg, "nao explica que selecionar clipe nao basta"
    assert "Entrevista FINAL" in msg, "nao lista as timelines disponiveis"
    assert "Edit" in msg, "nao diz onde abrir a timeline"


def test_projeto_sem_nenhuma_timeline(monkeypatch):
    _patch(monkeypatch, _project(current_timeline=None, timelines=[]))

    with pytest.raises(RuntimeError, match="nao tem nenhuma timeline"):
        dv._get_main_source()


def test_timeline_aberta_mas_vazia_diz_o_nome_dela(monkeypatch):
    tl = FakeTimeline("Timeline Vazia", items=[])
    _patch(monkeypatch, _project(current_timeline=tl))

    with pytest.raises(RuntimeError) as e:
        dv._get_main_source()

    assert "Timeline Vazia" in str(e.value), "nao diz qual timeline esta aberta"


def test_listar_timelines_e_1_based_e_nao_quebra(monkeypatch):
    tls = [FakeTimeline("A"), FakeTimeline("B"), FakeTimeline("C")]
    assert dv.list_timelines(_project(timelines=tls)) == ["A", "B", "C"]


def test_listar_timelines_tolera_api_indisponivel():
    """Nunca derruba o fluxo por causa da listagem (e so ajuda de mensagem)."""
    quebrado = SimpleNamespace(
        GetTimelineCount=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert dv.list_timelines(quebrado) == []
