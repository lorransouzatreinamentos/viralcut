"""
Calcula o PremiereCutPlan (ver PLANO_MESTRE.md secoes 8, 9.3, 10).

O Core NAO toca no Premiere. Ele so calcula um plano (ticks + offsets + cor) que
o painel CEP materializa via ExtendScript. Toda a aritmetica de ticks vive aqui,
em funcoes puras testaveis -- o mesmo padrao usado no adapter DaVinci, pela mesma
razao: erro de 1 frame/tick e silencioso e so aparece na revisao manual.

Fatos de plataforma (pesquisa das APIs reais):
 - 254016000000 ticks por segundo (numero escolhido pela Adobe para dividir
   exatamente todos os frame rates comuns: 24/25/29.97/30/50/59.94/60).
 - createSubClip espera ticks como STRING (ES3 nao tem inteiro 64-bit).
 - a cor vai no projectItem do subclip via setColorLabel(index) -- NUNCA no
   trackItem (o bug do FastVideo: trackItem nao tem setColorLabel).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

TICKS_PER_SECOND = 254016000000

# Mapeia as cores nomeadas do modelo (compat. DaVinci) para indices de label do
# Premiere (0-15). Mapeamento default verificado do Premiere:
#   0 Violet 1 Iris 2 Caribbean 3 Lavender 4 Cerulean 5 Forest 6 Rose 7 Mango
#   8 Purple 9 Blue 10 Teal 11 Magenta 12 Tan 13 Green 14 Brown 15 Yellow
# (o usuario pode renomear labels em Preferences>Labels; o indice permanece).
_COLOR_TO_PREMIERE_INDEX = {
    "Blue": 9,
    "Purple": 8,
    "Orange": 7,     # Mango
    "Green": 13,
    "Pink": 6,       # Rose
    "Teal": 10,
}
_DEFAULT_LABEL_INDEX = 2  # Caribbean — cor de destaque distinta para cortes virais


def seconds_to_frame_snapped_ticks(sec: float, fps: float, direction: str = "in") -> str:
    """Converte segundos para ticks-string, GRAMPEADO na fronteira de frame.

    Arredondamento DIRECIONAL (igual ao core-cep.js e ao build_clip_infos): a
    entrada usa floor, a saida usa ceil -- o corte nunca encolhe (round nos dois
    lados perdia ate meio frame no fim, decepando a silaba final).
    """
    frame = math.floor(sec * fps) if direction == "in" else math.ceil(sec * fps)
    ticks = round(frame * TICKS_PER_SECOND / fps)
    return str(ticks)


def _label_index(color_name: str) -> int:
    return _COLOR_TO_PREMIERE_INDEX.get(color_name, _DEFAULT_LABEL_INDEX)


@dataclass
class SeqItemDesc:
    """Descritor de um trackItem da sequencia (o painel CEP preenche em segundos)."""

    start: float          # inicio na sequencia (segundos)
    end: float            # fim na sequencia (segundos)
    in_point: float       # in-point na midia de origem (segundos)
    project_item_id: str  # nodeId do projectItem de origem
    name: str = ""


def _find_item_containing(items: list[SeqItemDesc], sec: float) -> SeqItemDesc | None:
    for it in items:
        if it.start <= sec < it.end:
            return it
    return None


def build_cut_plan(
    clips: list,
    seq_items: list[SeqItemDesc],
    fps: float,
    new_sequence_name: str,
) -> dict:
    """Monta o PremiereCutPlan a partir dos cortes (start/end em segundos de sequencia).

    Cada corte vira: {project_item_id, in_ticks, out_ticks, offset_sec, label_index, ...}
    - in_ticks/out_ticks referenciam a MIDIA DE ORIGEM (mapeado via in_point do item).
    - offset_sec e a posicao na NOVA sequencia (cortes encaixados sequencialmente).

    Cortes que nao mapeiam sao pulados com warning (mesma politica do DaVinci).
    """
    cuts: list[dict] = []
    warnings: list[str] = []
    running_offset = 0.0

    for clip in clips:
        item = _find_item_containing(seq_items, clip.start)
        if item is None:
            warnings.append(f"corte '{clip.titulo}' ({clip.start:.1f}s): sem clip na sequencia nesse ponto — pulado")
            continue

        # Grampeia ao fim do item de origem se o corte cruza a fronteira
        clamped_end = min(clip.end, item.end)
        if clamped_end < clip.end:
            warnings.append(f"corte '{clip.titulo}': cruza fronteira de clip — grampeado ao fim do clip")

        # sequencia -> origem: source_time = in_point + (seq_time - item.start)
        source_in = item.in_point + (clip.start - item.start)
        source_out = item.in_point + (clamped_end - item.start)

        duration = clamped_end - clip.start
        if duration <= 0:
            warnings.append(f"corte '{clip.titulo}': duracao nula apos mapeamento — pulado")
            continue

        cuts.append({
            "id": clip.id,
            "titulo": clip.titulo,
            "project_item_id": item.project_item_id,
            "in_ticks": seconds_to_frame_snapped_ticks(source_in, fps, "in"),
            "out_ticks": seconds_to_frame_snapped_ticks(source_out, fps, "out"),
            "offset_sec": running_offset,
            "label_index": _label_index(clip.color),
        })
        running_offset += duration

    return {
        "new_sequence_name": new_sequence_name,
        "cuts": cuts,
        "warnings": warnings,
    }
