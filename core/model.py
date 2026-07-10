"""
Modelo de dados canônico do VIRALCUT (ver PLANO_MESTRE.md secoes 1.1, 7, 12).

Regra central (correcao do bug primordial do FastVideo): a IA NUNCA fornece
um timecode numerico. Os schemas *Raw* que a IA preenche so tem campos de ID
(extra="forbid" impede a IA de "colar" um start/end por engano). Timecodes
sao sempre calculados aqui, a partir da lista de palavras transcritas.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Word(BaseModel):
    id: int
    text: str
    start: float
    end: float

    @model_validator(mode="after")
    def _check_order(self) -> "Word":
        if self.end <= self.start:
            raise ValueError(f"word {self.id}: end ({self.end}) <= start ({self.start})")
        return self


class Segment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    word_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_order(self) -> "Segment":
        if self.end <= self.start:
            raise ValueError(f"segment {self.id}: end ({self.end}) <= start ({self.start})")
        return self


class TranscriptState(BaseModel):
    """Transcricao completa de uma timeline: a fonte de verdade de tempo."""

    words: list[Word] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "TranscriptState":
        word_ids = [w.id for w in self.words]
        if len(word_ids) != len(set(word_ids)):
            raise ValueError("word ids duplicados em TranscriptState")
        seg_ids = [s.id for s in self.segments]
        if len(seg_ids) != len(set(seg_ids)):
            raise ValueError("segment ids duplicados em TranscriptState")
        return self

    def word_by_id(self, word_id: int) -> Word:
        for w in self.words:
            if w.id == word_id:
                return w
        raise KeyError(f"word_id {word_id} nao existe na transcricao")

    def segment_by_id(self, segment_id: int) -> Segment:
        for s in self.segments:
            if s.id == segment_id:
                return s
        raise KeyError(f"segment_id {segment_id} nao existe na transcricao")


# ---------------------------------------------------------------------------
# Cortes virais "diretos" (highlight) — Fase 2
# ---------------------------------------------------------------------------

class LLMHighlightRaw(BaseModel):
    """Schema que a IA preenche. Propositalmente SEM campo start/end numerico.

    extra="forbid": se o modelo tentar incluir um campo extra (ex.: "start"),
    a validacao falha explicitamente em vez de silenciosamente aceitar um
    timecode inventado. Isso e a correcao estrutural do bug do FastVideo
    (ver PLANO_MESTRE.md 1.1) — nao depende de o prompt ser obedecido.
    """

    model_config = ConfigDict(extra="forbid")

    start_seg_id: int
    end_seg_id: int
    titulo: str
    motivo: str = ""
    hook_first_3s: str = ""
    score: int = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def _check_order(self) -> "LLMHighlightRaw":
        if self.end_seg_id < self.start_seg_id:
            raise ValueError("end_seg_id < start_seg_id")
        return self


class HighlightClip(BaseModel):
    """Corte viral resolvido: start/end SEMPRE derivados de word ids."""

    id: str
    type: Literal["highlight"] = "highlight"
    titulo: str
    start_word_id: int
    end_word_id: int
    start: float
    end: float
    score: int = Field(ge=0, le=100)
    motivo: str = ""
    hook_first_3s: str = ""
    text: str = ""
    approved: bool = False
    color: str = "Blue"

    @model_validator(mode="after")
    def _check_order(self) -> "HighlightClip":
        if self.end_word_id < self.start_word_id:
            raise ValueError("end_word_id < start_word_id")
        if self.end <= self.start:
            raise ValueError("end <= start")
        return self


# Padding ADAPTATIVO (mesma logica dos silencios em objectives.py e do adaptivePad
# no core-cep.js). O `end` do Whisper e o fim do fonema, nao do som audivel; um
# padding fixo pequeno decepa a ultima silaba (o "come palavras"). A cauda estende
# ate a proxima palavra falada, sem invadi-la.
_HEAD_PAD_MAX = 0.10
_TAIL_PAD_MAX = 0.25
_TAIL_PAD_RATIO = 0.8


def resolve_highlight(
    raw: LLMHighlightRaw,
    transcript: TranscriptState,
    clip_id: str,
) -> HighlightClip:
    """Unico caminho para transformar uma proposta da IA num corte aplicavel.

    Le os IDs de segmento propostos pela IA, resolve para o primeiro/ultimo
    word_id daquele intervalo de segmentos, e calcula start/end A PARTIR das
    palavras (nunca de um numero que a IA tenha escrito).
    """
    start_seg = transcript.segment_by_id(raw.start_seg_id)
    end_seg = transcript.segment_by_id(raw.end_seg_id)

    if not start_seg.word_ids or not end_seg.word_ids:
        raise ValueError(
            f"segmento {start_seg.id} ou {end_seg.id} sem word_ids — "
            "transcricao precisa ser word-level para gerar cortes"
        )

    start_word_id = start_seg.word_ids[0]
    end_word_id = end_seg.word_ids[-1]

    start_word = transcript.word_by_id(start_word_id)
    end_word = transcript.word_by_id(end_word_id)

    # padding adaptativo: usa a folga real ate a palavra vizinha (nunca invade)
    prev_end = max((w.end for w in transcript.words if w.end <= start_word.start), default=0.0)
    next_start = min((w.start for w in transcript.words if w.start >= end_word.end),
                     default=end_word.end + _TAIL_PAD_MAX)
    head = min(_HEAD_PAD_MAX, max(0.0, start_word.start - prev_end) * 0.5)
    tail = min(_TAIL_PAD_MAX, max(0.0, next_start - end_word.end) * _TAIL_PAD_RATIO)
    start = max(0.0, start_word.start - head)
    end = end_word.end + tail

    # texto completo dos segmentos (nao reconstruido das palavras, que o Whisper
    # as vezes omite deixando o preview picotado)
    seg_texts = []
    for sid in range(raw.start_seg_id, raw.end_seg_id + 1):
        try:
            seg_texts.append(transcript.segment_by_id(sid).text)
        except KeyError:
            pass

    return HighlightClip(
        id=clip_id,
        titulo=raw.titulo,
        start_word_id=start_word_id,
        end_word_id=end_word_id,
        start=start,
        end=end,
        score=raw.score,
        motivo=raw.motivo,
        hook_first_3s=raw.hook_first_3s,
        text=" ".join(seg_texts),
    )


# ---------------------------------------------------------------------------
# Estado do projeto (Fase 0: so o essencial para persistir uma analise)
# ---------------------------------------------------------------------------

class MediaInfo(BaseModel):
    source: Literal["premiere", "davinci"]
    clip_ref: str = ""
    fps: float = 30.0
    duration_sec: float = 0.0
    audio_export_path: str = ""


class ProjectState(BaseModel):
    schema_version: str = "1.0"
    media: MediaInfo | None = None
    transcript: TranscriptState = Field(default_factory=TranscriptState)
    clips: list[HighlightClip] = Field(default_factory=list)
    # ponytail: frankenbite (Fase 7) e gaps (Fase 6) entram aqui quando construidos
