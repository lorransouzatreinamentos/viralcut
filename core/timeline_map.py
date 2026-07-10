"""
Timeline <-> origem: costura as transcricoes dos varios videos de uma timeline
numa unica transcricao em tempo-de-TIMELINE, e faz o caminho de volta (um corte
em tempo-de-timeline vira sub-trechos em tempo-de-origem, um por clipe que ele
atravessa).

Por que isso existe: o app transcreve cada ARQUIVO DE ORIGEM (com cache por
arquivo), nao a timeline renderizada. Uma timeline com 5 videos tem 5 fontes.
Sem este modulo, o app so via um video. Aqui remapeamos cada fonte para a
posicao dela na timeline e concatenamos -- a IA passa a enxergar a fala inteira.

Premissa (ponytail): velocidade 1x, sem retime. duracao-na-timeline == duracao
na origem para o mesmo trecho. Se surgir speed ramp, tratar aqui (fator =
dur_timeline/dur_origem) -- por ora nao ocorre no fluxo do usuario.

Este arquivo e ESPELHADO em premiere-panel/client/core-cep.js (mesmas regras,
mesmos nomes) para o painel Premiere produzir resultado identico. Manter os dois
em sincronia; os testes de timeline_map travam o contrato.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.model import Segment, TranscriptState, Word

# Tolerancia ao decidir se uma palavra pertence ao trecho usado do clipe. Whisper
# marca o fim da palavra no fonema, nao no som audivel; uma folga evita perder a
# palavra bem na borda do in/out point do clipe.
_EDGE_TOL = 0.05


@dataclass
class TimelineClip:
    """Um clipe de video posicionado na timeline.

    tl_start / tl_end: posicao NA TIMELINE (segundos).
    src_in: in-point na MIDIA DE ORIGEM (segundos) -- onde no arquivo o trecho
            usado comeca. O out de origem e src_in + (tl_end - tl_start).
    source_key: caminho do arquivo (chave da transcricao/cache).
    ref: identidade da fonte para o apply (path no DaVinci, project_item_id no
         Premiere). Pode ser igual a source_key.
    """

    source_key: str
    ref: str
    src_in: float
    tl_start: float
    tl_end: float
    name: str = ""

    @property
    def duration(self) -> float:
        return self.tl_end - self.tl_start

    @property
    def src_out(self) -> float:
        return self.src_in + self.duration


def remap_to_timeline(
    clips: list[TimelineClip],
    transcripts: dict[str, TranscriptState],
) -> TranscriptState:
    """Costura as transcricoes (uma por arquivo-fonte) numa transcricao unica em
    tempo-de-timeline. Words e segments recebem ids novos e contiguos.

    clips: os clipes da timeline, na ordem que quiser (a saida e ordenada por
           tempo de timeline no fim).
    transcripts: {source_key -> TranscriptState em tempo-de-ORIGEM}.
    """
    out_words: list[Word] = []
    out_segments: list[Segment] = []

    # Ordena por posicao na timeline para a transcricao final ser cronologica.
    for clip in sorted(clips, key=lambda c: c.tl_start):
        t = transcripts.get(clip.source_key)
        if t is None or not t.segments:
            continue

        lo = clip.src_in - _EDGE_TOL
        hi = clip.src_out + _EDGE_TOL
        shift = clip.tl_start - clip.src_in  # t_timeline = t_origem + shift

        # Reindexa as palavras deste clipe que caem no trecho usado.
        old_to_new: dict[int, int] = {}
        for w in t.words:
            if w.end <= lo or w.start >= hi:
                continue  # fora do trecho que este clipe usa
            new_id = len(out_words)
            old_to_new[w.id] = new_id
            ns = max(0.0, w.start + shift)
            ne = w.end + shift
            if ne <= ns:
                ne = ns + 0.01  # guarda o invariante end>start do modelo
            out_words.append(Word(id=new_id, text=w.text, start=ns, end=ne))

        # Reconstroi os segments so com as palavras que sobreviveram ao filtro.
        for s in t.segments:
            kept = [old_to_new[wid] for wid in s.word_ids if wid in old_to_new]
            if not kept:
                continue
            seg_start = min(out_words[k].start for k in kept)
            seg_end = max(out_words[k].end for k in kept)
            if seg_end <= seg_start:
                seg_end = seg_start + 0.01
            out_segments.append(Segment(
                id=len(out_segments), start=seg_start, end=seg_end,
                text=s.text, word_ids=kept,
            ))

    return TranscriptState(words=out_words, segments=out_segments)


@dataclass
class SourceSpan:
    """Um pedaco de um corte, ja mapeado para uma fonte especifica."""

    ref: str          # path (DaVinci) ou project_item_id (Premiere)
    source_key: str
    src_start: float  # segundos na midia de origem
    src_end: float
    tl_start: float   # posicao na timeline (para ordenar os pedacos)


def split_cut(
    cut_start: float,
    cut_end: float,
    clips: list[TimelineClip],
) -> list[SourceSpan]:
    """Um corte em tempo-de-timeline [cut_start, cut_end] vira N spans em
    tempo-de-origem, um por clipe que ele atravessa (na ordem da timeline).

    Um corte que cai inteiro dentro de um clipe -> 1 span. Um corte que cruza a
    edicao entre dois videos -> 2 spans (cada um na sua fonte). Isso e o que faz
    "remover silencios" e cortes longos funcionarem numa timeline multi-video,
    em vez de grampear tudo no primeiro clipe.
    """
    spans: list[SourceSpan] = []
    for clip in sorted(clips, key=lambda c: c.tl_start):
        seg_start = max(cut_start, clip.tl_start)
        seg_end = min(cut_end, clip.tl_end)
        if seg_end <= seg_start:
            continue  # sem interseccao com este clipe
        src_start = clip.src_in + (seg_start - clip.tl_start)
        src_end = clip.src_in + (seg_end - clip.tl_start)
        spans.append(SourceSpan(
            ref=clip.ref, source_key=clip.source_key,
            src_start=src_start, src_end=src_end, tl_start=seg_start,
        ))
    return spans
