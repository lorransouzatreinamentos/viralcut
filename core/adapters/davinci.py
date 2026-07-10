"""
Adapter DaVinci Resolve (ver PLANO_MESTRE.md secao 11). Requer Resolve **Studio**
aberto e as env vars RESOLVE_SCRIPT_API/RESOLVE_SCRIPT_LIB/PYTHONPATH configuradas.

Todas as funcoes aqui sao SINCRONAS (a API do Resolve e sincrona) -- quem chama
a partir do FastAPI deve rodar via asyncio.to_thread para nao bloquear o event loop.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Any


def _bootstrap():
    try:
        import DaVinciResolveScript as dvr  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "DaVinciResolveScript nao encontrado. Confirme RESOLVE_SCRIPT_API / "
            "RESOLVE_SCRIPT_LIB / PYTHONPATH no .env e que o Resolve Studio esta aberto "
            "(ver PLANO_MESTRE.md secao 19)."
        ) from e
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError("Nao foi possivel conectar ao DaVinci Resolve. Ele esta aberto?")
    return resolve


def _current_project(resolve):
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise RuntimeError("Nenhum projeto aberto no Resolve.")
    return project


def get_active_timeline_info() -> dict:
    """Le a timeline ativa. Nao modifica nada (ver PLANO_MESTRE.md secao 3.1)."""
    resolve = _bootstrap()
    project = _current_project(resolve)
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("Nenhuma timeline ativa. Abra uma timeline na pagina Edit.")

    fps = float(timeline.GetSetting("timelineFrameRate"))
    start_frame = timeline.GetStartFrame()
    end_frame = timeline.GetEndFrame()

    return {
        "name": timeline.GetName(),
        "fps": fps,
        "start_frame": start_frame,
        "end_frame": end_frame,
        "duration_sec": (end_frame - start_frame) / fps if fps else 0.0,
        "video_tracks": timeline.GetTrackCount("video"),
        "audio_tracks": timeline.GetTrackCount("audio"),
    }


def export_timeline_audio(output_dir: str, filename: str = "viralcut_audio", poll_interval_sec: float = 0.5) -> str:
    """Renderiza o audio da timeline ativa em WAV via Deliver (PLANO_MESTRE.md secao 11).

    WAV e grande (nao cabe direto no limite do Whisper) -- comprimir/dividir
    e responsabilidade de core.transcribe, nao deste adapter.
    """
    resolve = _bootstrap()
    project = _current_project(resolve)
    os.makedirs(output_dir, exist_ok=True)

    ok = project.SetRenderSettings({
        "SelectAllFrames": True,
        "TargetDir": output_dir,
        "CustomName": filename,
        "AudioCodec": "lpcm",
        "ExportVideo": False,
        "ExportAudio": True,
    })
    if not ok:
        raise RuntimeError("project.SetRenderSettings falhou.")

    if not project.SetCurrentRenderFormatAndCodec("wav", "lpcm"):
        raise RuntimeError("Nao foi possivel selecionar formato de render WAV/lpcm.")

    job_id = project.AddRenderJob()
    if not job_id:
        raise RuntimeError("Falha ao criar render job no Resolve (AddRenderJob retornou vazio).")

    if not project.StartRendering(job_id):
        raise RuntimeError(f"project.StartRendering({job_id}) retornou falso.")

    while project.IsRenderingInProgress():
        time.sleep(poll_interval_sec)

    status = project.GetRenderJobStatus(job_id) or {}
    if status.get("JobStatus") != "Complete":
        raise RuntimeError(f"Render de audio nao completou: {status}")

    output_path = os.path.join(output_dir, f"{filename}.wav")
    if not os.path.exists(output_path):
        raise RuntimeError(
            f"Render reportou sucesso mas o arquivo nao existe em {output_path}. "
            "TargetDir/CustomName podem ter sido ignorados pelo Resolve."
        )
    return output_path


# ---------------------------------------------------------------------------
# Aplicar cortes (Fase 3) — mapeamento tempo->frame isolado em funcao PURA
# ---------------------------------------------------------------------------
#
# O erro nº1 da API do Resolve (PLANO_MESTRE.md secao 11): clipInfo.startFrame/
# endFrame referenciam a MIDIA DE ORIGEM, nao a timeline. A transcricao, porem,
# esta em tempo de TIMELINE (o audio renderizado comeca no primeiro frame da
# timeline). Entao precisamos: tempo-audio -> frame-timeline -> frame-origem.
#
# Toda essa aritmetica vive em build_clip_infos (pura, testavel sem Resolve).
# A conversa com a API do Resolve e so uma casca fina em volta dela.


@dataclass
class TimelineItemDesc:
    """Descritor de um clip na timeline. media_pool_item e opaco (objeto do Resolve)."""

    start: int          # frame de inicio na timeline (absoluto, inclui GetStartFrame)
    end: int            # frame de fim na timeline (exclusivo, convencao do GetEnd)
    left_offset: int    # in-point dentro da midia de origem (GetLeftOffset)
    media_pool_item: Any
    name: str = ""


def build_clip_infos(
    clips: list,
    timeline_items: list[TimelineItemDesc],
    timeline_start_frame: int,
    fps: float,
    min_frame_dur: int = 1,
) -> tuple[list[dict], list[str]]:
    """Converte cortes (start/end em segundos de TIMELINE) em clipInfo do Resolve.

    Retorna (clip_infos, warnings). Um corte que nao mapeia (cai num gap, ou o
    item de origem nao tem midia) e pulado com um warning em vez de derrubar
    a operacao inteira.

    Um corte que CRUZA a fronteira entre dois videos da timeline vira VARIOS
    clipInfos consecutivos, um por item que ele atravessa, todos com a mesma cor
    e _clip_id. Sem isso, uma timeline com 5 videos so cortava dentro do primeiro
    (bug reportado). A ordem dos itens na timeline e preservada.
    """
    clip_infos: list[dict] = []
    warnings: list[str] = []
    ordered_items = sorted(timeline_items, key=lambda it: it.start)

    for clip in clips:
        # arredondamento DIRECIONAL (igual ao core-cep.js): floor na entrada, ceil na
        # saida -- o corte nunca encolhe. round nos dois lados perdia ate meio frame no
        # fim, decepando a silaba final.
        cut_start_tl = timeline_start_frame + math.floor(clip.start * fps)
        cut_end_tl = timeline_start_frame + math.ceil(clip.end * fps)

        intersected = False
        for item in ordered_items:
            seg_start = max(cut_start_tl, item.start)
            seg_end = min(cut_end_tl, item.end)
            if seg_end <= seg_start:
                continue  # este item nao participa deste corte
            intersected = True
            if item.media_pool_item is None:
                warnings.append(f"corte '{clip.titulo}': trecho sobre item sem midia de origem — pulado")
                continue

            source_in = item.left_offset + (seg_start - item.start)
            source_out = item.left_offset + (seg_end - item.start)
            if source_out - source_in < min_frame_dur:
                continue  # fatia menor que 1 frame na fronteira; ignora em silencio

            clip_infos.append({
                "mediaPoolItem": item.media_pool_item,
                "startFrame": source_in,
                "endFrame": source_out,
                "_clip_id": clip.id,     # so p/ correlacao/logs; removido antes de enviar ao Resolve
                "_color": clip.color,
            })

        if not intersected:
            warnings.append(f"corte '{clip.titulo}' ({clip.start:.1f}s): sem clip na timeline nesse ponto — pulado")

    return clip_infos, warnings


def _find_item_containing(items: list[TimelineItemDesc], frame: int) -> TimelineItemDesc | None:
    for it in items:
        if it.start <= frame < it.end:
            return it
    return None


def _read_video_track_items(timeline, track_index: int = 1) -> list[TimelineItemDesc]:
    descs: list[TimelineItemDesc] = []
    for it in timeline.GetItemListInTrack("video", track_index) or []:
        descs.append(TimelineItemDesc(
            start=int(it.GetStart()),
            end=int(it.GetEnd()),
            left_offset=int(it.GetLeftOffset()),
            media_pool_item=it.GetMediaPoolItem(),
            name=it.GetName() or "",
        ))
    return descs


def apply_cuts(clips: list, new_timeline_name: str) -> dict:
    """Cria uma NOVA timeline com os cortes aprovados, cada um colorido.
    A timeline original nunca e tocada (PLANO_MESTRE.md secao 9.3).
    """
    if not clips:
        raise RuntimeError("Nenhum corte aprovado para aplicar.")

    resolve = _bootstrap()
    project = _current_project(resolve)
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("Nenhuma timeline ativa.")

    fps = float(timeline.GetSetting("timelineFrameRate"))
    tl_start = int(timeline.GetStartFrame())
    items = _read_video_track_items(timeline)
    if not items:
        raise RuntimeError("Timeline ativa nao tem clips na track de video 1.")

    clip_infos, warnings = build_clip_infos(clips, items, tl_start, fps)
    if not clip_infos:
        raise RuntimeError("Nenhum corte pode ser mapeado para a midia de origem. " + " | ".join(warnings))

    # Guarda cor/id antes de limpar os campos internos que o Resolve nao entende
    colors = [ci.pop("_color") for ci in clip_infos]
    for ci in clip_infos:
        ci.pop("_clip_id", None)

    media_pool = project.GetMediaPool()
    new_timeline = media_pool.CreateTimelineFromClips(new_timeline_name, clip_infos)
    if new_timeline is None:
        raise RuntimeError("CreateTimelineFromClips retornou None — Resolve recusou a criacao da timeline.")

    # Colore cada clip da nova timeline (SetClipColor existe no TimelineItem — ao
    # contrario do Premiere; ver PLANO_MESTRE.md secao 3.1)
    new_items = new_timeline.GetItemListInTrack("video", 1) or []
    colored = 0
    for item, color in zip(new_items, colors):
        if item.SetClipColor(color):
            colored += 1

    # Verificacao pos-aplicacao (secao 2, principio 4): reler e conferir contagem
    expected = len(clip_infos)
    actual = len(new_items)
    if actual != expected:
        warnings.append(f"esperava {expected} clips na nova timeline, encontrei {actual}")

    return {
        "applied": actual,
        "expected": expected,
        "colored": colored,
        "new_timeline_name": new_timeline_name,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Fluxo por ARQUIVO-FONTE (igual ao Premiere): transcreve o arquivo do clip
# principal; cortes ja saem em tempo-de-origem, prontos p/ virar frames.
# ---------------------------------------------------------------------------

# Cores nomeadas do DaVinci (SetClipColor)
_DAVINCI_COLORS = ["Blue", "Purple", "Orange", "Green", "Pink", "Teal", "Yellow", "Navy"]


def _set_output_folder(media_pool, name: str):
    """Cria (ou reusa) uma subpasta no Media Pool e a torna a atual, para as
    timelines novas nascerem organizadas em vez de soltas na raiz."""
    try:
        root = media_pool.GetRootFolder()
        target = None
        for f in root.GetSubFolderList() or []:
            if f.GetName() == name:
                target = f
                break
        if target is None:
            target = media_pool.AddSubFolder(root, name)
        if target:
            media_pool.SetCurrentFolder(target)
    except Exception:  # noqa: BLE001 — organizacao e best-effort, nunca derruba o apply
        pass


def list_timelines(project) -> list[str]:
    """Nomes das timelines existentes no projeto (indice e 1-based na API)."""
    names: list[str] = []
    try:
        for i in range(1, (project.GetTimelineCount() or 0) + 1):
            tl = project.GetTimelineByIndex(i)
            if tl:
                names.append(tl.GetName())
    except Exception:  # noqa: BLE001 — listagem e so para a mensagem de ajuda
        pass
    return names


def list_timeline_clips() -> dict:
    """TODOS os clipes de video da timeline aberta, em SEGUNDOS de timeline/origem.

    Corrige o bug de "so analisou o primeiro video": antes o app pegava so o clipe
    mais longo. Agora devolve cada clipe para o Core transcrever a fala inteira.

    Retorno JSON-safe:
      { timeline_name, duration_sec, fps,
        clips: [{source_key, src_in, tl_start, tl_end, name}],
        sources: [paths unicos] }
    """
    resolve = _bootstrap()
    project = _current_project(resolve)
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        nomes = list_timelines(project)
        if nomes:
            lista = ", ".join(f'"{n}"' for n in nomes[:6])
            raise RuntimeError(
                "Nenhuma timeline ABERTA. Selecionar um clipe no Media Pool nao basta — "
                "o VIRALCUT trabalha sobre a timeline aberta na pagina Edit. "
                f"Timelines neste projeto: {lista}. Va na pagina Edit e de dois cliques em uma."
            )
        raise RuntimeError("Este projeto nao tem nenhuma timeline. Arraste seu video para a pagina Edit.")

    tl_fps = float(timeline.GetSetting("timelineFrameRate"))
    tl_start = int(timeline.GetStartFrame())

    clips: list[dict] = []
    sources: list[str] = []
    for t in range(1, timeline.GetTrackCount("video") + 1):
        for item in timeline.GetItemListInTrack("video", t) or []:
            mpi = item.GetMediaPoolItem()
            if mpi is None:
                continue
            props = mpi.GetClipProperty() or {}
            path = props.get("File Path") or mpi.GetClipProperty("File Path")
            if not path:
                continue
            try:
                src_fps = float(props.get("FPS") or tl_fps)
            except (TypeError, ValueError):
                src_fps = tl_fps
            # Posicao na timeline (segundos, relativa ao inicio da timeline).
            tl_s = (int(item.GetStart()) - tl_start) / tl_fps
            tl_e = (int(item.GetEnd()) - tl_start) / tl_fps
            # In-point na origem (GetLeftOffset em frames de origem -> segundos).
            src_in = int(item.GetLeftOffset()) / src_fps
            clips.append({
                "source_key": path, "src_in": src_in,
                "tl_start": tl_s, "tl_end": tl_e,
                "name": item.GetName() or "",
            })
            if path not in sources:
                sources.append(path)

    if not clips:
        raise RuntimeError(
            f'A timeline aberta ("{timeline.GetName()}") nao tem nenhum clipe de video.'
        )

    duration = max(c["tl_end"] for c in clips)
    return {
        "timeline_name": timeline.GetName(), "fps": tl_fps,
        "duration_sec": duration, "clips": clips, "sources": sources,
    }


def apply_timeline_cuts(cuts: list[dict], new_timeline_name: str) -> dict:
    """Cria nova timeline com cortes em tempo de TIMELINE (segundos).

    cuts: [{start, end, color?, titulo?, id?}]. Casca fina sobre apply_cuts, que
    ja re-le a timeline e usa build_clip_infos (split multi-video). Um corte que
    cruza a edicao entre videos vira varios pedacos automaticamente.
    """
    from types import SimpleNamespace
    if not cuts:
        raise RuntimeError("Nenhum corte para aplicar.")
    objs = [
        SimpleNamespace(
            start=c["start"], end=c["end"],
            color=c.get("color", "Blue"),
            titulo=c.get("titulo", "corte"),
            id=c.get("id", f"c{i}"),
        )
        for i, c in enumerate(cuts)
    ]
    return apply_cuts(objs, new_timeline_name)
