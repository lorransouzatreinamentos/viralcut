"""Core Engine — servidor local do VIRALCUT (ver PLANO_MESTRE.md secao 8)."""
import asyncio
import json
import math
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.adapters import davinci
from core.adapters.premiere_plan import SeqItemDesc, build_cut_plan
from core.config import settings
from core.objectives import extract_montages, remove_silences
from core.transcribe import transcribe_timeline_audio
from core.viral import extract_viral_clips

app = FastAPI(title="VIRALCUT Core", version="0.1.0")

_UI_DIR = Path(__file__).resolve().parent.parent / "ui"
if _UI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")

TMP_DIR = Path(tempfile.gettempdir()) / "viralcut"
TMP_DIR.mkdir(parents=True, exist_ok=True)

# Estado em memoria (single-user, single-process -- ver secao 4.2; um DB seria
# over-engineering para uma ferramenta local de um usuario so).
_jobs: dict[str, dict] = {}
_state: dict = {"timeline": None, "transcript": None, "clips": {}, "premiere_source": None}

# Estado + log do fluxo DaVinci (browser UI -> Core Python -> Resolve)
_dv: dict = {"source": None, "transcript": None, "viral": [], "montages": [], "silences": None}
_LOG_DIR = Path.home() / ".viralcut" / "logs"


def _dv_log():
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "host": "davinci",
            "source": _dv["source"],
            "whisper": (
                {"words": len(_dv["transcript"].words), "segments": len(_dv["transcript"].segments),
                 "full_text": " ".join(s.text for s in _dv["transcript"].segments)}
                if _dv["transcript"] else None
            ),
            "viral": [{"titulo": c.titulo, "score": c.score, "start": c.start, "end": c.end, "text": c.text}
                      for c in _dv["viral"]],
            "montages": [{"titulo": m["titulo"], "score": m["score"], "pieces": len(m["pieces"]), "text": m["text"]}
                         for m in _dv["montages"]],
            "silences": _dv["silences"],
        }
        (_LOG_DIR / "last-run.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), "utf8")
    except Exception:  # noqa: BLE001 — log nunca derruba a operacao
        pass


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_version() -> str:
    import subprocess
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=V.%cd", "--date=format:%d.%m.%y.%H.%M"],
            capture_output=True, text=True, cwd=str(_REPO_ROOT), timeout=10,
        )
        v = out.stdout.strip()
        return v or "dev"
    except Exception:  # noqa: BLE001
        return "dev"


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}


@app.get("/version")
def version():
    return {"version": _git_version()}


@app.post("/update")
def update():
    """Auto-update do app DaVinci: git pull. Com uvicorn --reload, o servidor
    recarrega o código sozinho; o browser dá reload e pega a versão nova."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "pull", "--ff-only"], capture_output=True, text=True,
            cwd=str(_REPO_ROOT), timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"git pull falhou: {e}") from e
    if out.returncode != 0:
        raise HTTPException(status_code=500, detail=f"git pull falhou: {out.stderr[-300:]}")
    return {"ok": True, "output": out.stdout.strip(), "version": _git_version()}


@app.get("/host")
def host():
    """Detecta se o DaVinci Resolve Studio esta acessivel a partir deste processo.

    O Premiere e detectado do lado do painel CEP (que ja sabe onde esta rodando);
    aqui so reportamos a capacidade que o Core PODE dirigir diretamente.
    """
    davinci_studio = _detect_davinci_studio()
    return {
        "davinci_studio_available": davinci_studio,
        "port": settings.port,
    }


@app.post("/panel/update")
def panel_update():
    """Reinstala o painel a partir do código-fonte atual (puxa a versão nova).
    Acionado pelo link no título. Atualiza HTML/CSS/JS na hora (reload);
    mudanca no host ExtendScript ainda exige reiniciar o Premiere."""
    import subprocess

    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "install-premiere.sh"
    if not script.exists():
        raise HTTPException(status_code=500, detail="install-premiere.sh nao encontrado")
    try:
        out = subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, timeout=60, cwd=str(root)
        )
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=500, detail="update expirou") from e
    if out.returncode != 0:
        raise HTTPException(status_code=500, detail=f"update falhou: {out.stderr[-300:]}")
    return {"ok": True, "output": out.stdout.strip()}


def _detect_davinci_studio() -> bool:
    has_env = bool(os.getenv("RESOLVE_SCRIPT_API")) and bool(os.getenv("RESOLVE_SCRIPT_LIB"))
    if not has_env:
        return False
    try:
        import DaVinciResolveScript  # noqa: F401
    except ImportError:
        return False
    return True


@app.post("/timeline/select")
async def timeline_select():
    """Le a timeline ativa do DaVinci (nao modifica nada). Ver secao 9.1."""
    try:
        info = await asyncio.to_thread(davinci.get_active_timeline_info)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _state["timeline"] = info
    return info


class TranscribeRequest(BaseModel):
    language: str = "pt"


@app.post("/transcribe")
async def transcribe_start(req: TranscribeRequest):
    if _state["timeline"] is None:
        raise HTTPException(status_code=400, detail="Nenhuma timeline selecionada. Chame /timeline/select primeiro.")

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "progress": 0}
    asyncio.create_task(_run_transcription(job_id, req.language))
    return {"job_id": job_id}


async def _run_transcription(job_id: str, language: str):
    try:
        _jobs[job_id]["progress"] = 10
        audio_path = await asyncio.to_thread(
            davinci.export_timeline_audio, str(TMP_DIR), f"seq_{job_id}"
        )
        _jobs[job_id]["progress"] = 50
        transcript = await transcribe_timeline_audio(audio_path, str(TMP_DIR), language=language)
        _state["transcript"] = transcript
        _jobs[job_id] = {
            "status": "done",
            "progress": 100,
            "words": len(transcript.words),
            "segments": len(transcript.segments),
        }
    except Exception as e:  # noqa: BLE001 — job assincrono: erro vira estado, nao excecao solta
        _jobs[job_id] = {"status": "error", "progress": 0, "error": str(e)}


class TranscribeFileRequest(BaseModel):
    """Fluxo Premiere: transcreve o ARQUIVO DE ORIGEM direto (via getMediaPath).
    Timecodes saem em tempo-de-origem, prontos para createSubClip."""

    path: str
    fps: float
    project_item_id: str
    duration_sec: float = 0.0
    language: str = "pt"


@app.post("/transcribe/file")
async def transcribe_file_start(req: TranscribeFileRequest):
    if not os.path.exists(req.path):
        raise HTTPException(status_code=400, detail=f"Arquivo de midia nao encontrado: {req.path}")

    _state["premiere_source"] = {
        "project_item_id": req.project_item_id,
        "fps": req.fps,
        "duration_sec": req.duration_sec,
    }
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "progress": 0}
    asyncio.create_task(_run_file_transcription(job_id, req.path, req.language))
    return {"job_id": job_id}


async def _run_file_transcription(job_id: str, path: str, language: str):
    try:
        _jobs[job_id]["progress"] = 30
        transcript = await transcribe_timeline_audio(path, str(TMP_DIR), language=language)
        _state["transcript"] = transcript
        _jobs[job_id] = {
            "status": "done",
            "progress": 100,
            "words": len(transcript.words),
            "segments": len(transcript.segments),
        }
    except Exception as e:  # noqa: BLE001
        _jobs[job_id] = {"status": "error", "progress": 0, "error": str(e)}


@app.get("/transcribe/{job_id}")
async def transcribe_status(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job nao encontrado")
    if job["status"] != "done":
        return job

    transcript = _state["transcript"]
    return {
        **job,
        "words": [w.model_dump() for w in transcript.words],
        "segments": [s.model_dump() for s in transcript.segments],
    }


class ViralClipsRequest(BaseModel):
    min_score: int = 50
    max_clips: int = 10
    min_dur: float = 20.0
    max_dur: float = 90.0


@app.post("/clips/viral")
async def clips_viral(req: ViralClipsRequest):
    """Extrai cortes virais da transcricao ja carregada. Ver secoes 1.1/9.2/12.1 --
    o timecode de cada corte vem SEMPRE das palavras reais, nunca de um numero
    que a IA tenha escrito."""
    if _state["transcript"] is None:
        raise HTTPException(status_code=400, detail="Nenhuma transcricao disponivel. Chame /transcribe primeiro.")

    try:
        clips, rejected = await extract_viral_clips(
            _state["transcript"],
            min_score=req.min_score,
            max_clips=req.max_clips,
            min_dur=req.min_dur,
            max_dur=req.max_dur,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    _state["clips"] = {c.id: c for c in clips}
    return {"clips": [c.model_dump() for c in clips], "rejected": rejected}


class ApproveRequest(BaseModel):
    clip_ids: list[str]


@app.post("/clips/approve")
async def clips_approve(req: ApproveRequest):
    clips = _state["clips"]
    approved = []
    for cid in req.clip_ids:
        if cid in clips:
            clips[cid].approved = True
            approved.append(cid)
    return {"ok": True, "approved": approved}


class PremiereSeqItem(BaseModel):
    start: float
    end: float
    in_point: float
    project_item_id: str
    name: str = ""


class PremiereContext(BaseModel):
    """Enviado pelo painel CEP: o Core calcula o plano, o painel materializa."""

    fps: float
    seq_items: list[PremiereSeqItem]


class ApplyRequest(BaseModel):
    clip_ids: list[str] | None = None  # None = todos os aprovados
    premiere: PremiereContext | None = None  # presente => host Premiere (retorna plano)


@app.post("/apply")
async def apply(req: ApplyRequest):
    """Materializa os cortes na NOVA timeline. Original intacta (PLANO_MESTRE.md 9.3).

    DaVinci: o Core dirige o Resolve direto e retorna o resultado.
    Premiere: o Core NAO toca no Premiere -- retorna um PremiereCutPlan que o
    painel CEP aplica via ExtendScript (secao 4.4).
    """
    all_clips = _state["clips"]
    if not all_clips:
        raise HTTPException(status_code=400, detail="Nenhum corte extraido. Chame /clips/viral primeiro.")

    if req.clip_ids is not None:
        selected = [all_clips[c] for c in req.clip_ids if c in all_clips]
    else:
        selected = [c for c in all_clips.values() if c.approved]

    if not selected:
        raise HTTPException(status_code=400, detail="Nenhum corte aprovado/selecionado para aplicar.")

    tl_name = _state["timeline"]["name"] if _state["timeline"] else "timeline"
    new_name = f"Cortes Virais — {tl_name}"

    # Premiere via transcricao de arquivo-fonte (getMediaPath): cortes ja estao em
    # tempo-de-origem. Um item sintetico cobrindo toda a midia faz build_cut_plan
    # (ja testada) mapear tempo->ticks direto, sem mapeamento timeline->origem.
    if _state["premiere_source"] is not None:
        src = _state["premiere_source"]
        synthetic = [SeqItemDesc(
            start=0.0,
            end=max(src["duration_sec"], 1e9),  # cobre a midia inteira
            in_point=0.0,
            project_item_id=src["project_item_id"],
        )]
        return build_cut_plan(selected, synthetic, src["fps"], new_name)

    # Premiere via descritores de sequencia (fluxo alternativo, se a UI os enviar)
    if req.premiere is not None:
        seq_items = [SeqItemDesc(**i.model_dump()) for i in req.premiere.seq_items]
        return build_cut_plan(selected, seq_items, req.premiere.fps, new_name)

    try:
        result = await asyncio.to_thread(davinci.apply_cuts, selected, new_name)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return result


# ===========================================================================
# FLUXO DAVINCI — browser UI -> Core Python -> Resolve (Studio)
# Transcreve o arquivo-fonte 1x; aplica objetivos quantas vezes quiser.
# ===========================================================================


@app.post("/davinci/select")
async def dv_select():
    try:
        info = await asyncio.to_thread(davinci.get_source_media_path)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _dv.update({"source": info, "transcript": None, "viral": [], "montages": [], "silences": None})
    _dv_log()
    return info


@app.post("/davinci/transcribe")
async def dv_transcribe():
    if _dv["source"] is None:
        raise HTTPException(status_code=400, detail="Selecione a timeline primeiro.")
    path = _dv["source"]["path"]
    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail=f"Arquivo de origem nao encontrado: {path}")
    try:
        transcript = await transcribe_timeline_audio(path, str(TMP_DIR), language="pt")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Falha na transcricao: {e}") from e
    if not transcript.segments:
        raise HTTPException(status_code=400, detail="Nenhuma fala detectada no video.")
    _dv["transcript"] = transcript
    _dv_log()
    return {"words": len(transcript.words), "segments": len(transcript.segments)}


def _require_transcript():
    if _dv["transcript"] is None:
        raise HTTPException(status_code=400, detail="Transcreva primeiro.")
    return _dv["transcript"]


_DAVINCI_COLORS = ["Blue", "Purple", "Orange", "Green", "Pink", "Teal", "Yellow", "Navy"]


class DvViralRequest(BaseModel):
    min_dur: float = 30.0
    max_dur: float = 90.0
    max_clips: int = 12
    min_score: int = 45


class DvMontageRequest(BaseModel):
    n_videos: int = 3        # quantos videos montados o usuario quer
    min_dur: float = 30.0
    max_dur: float = 90.0


@app.post("/davinci/viral")
async def dv_viral(req: DvViralRequest | None = None):
    req = req or DvViralRequest()
    transcript = _require_transcript()
    try:
        clips, rejected = await extract_viral_clips(
            transcript, min_score=req.min_score, max_clips=req.max_clips,
            min_dur=req.min_dur, max_dur=req.max_dur,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    for i, c in enumerate(clips):
        c.color = _DAVINCI_COLORS[i % len(_DAVINCI_COLORS)]
    _dv["viral"] = clips
    _dv_log()
    return {"clips": [c.model_dump() for c in clips], "rejected": rejected}


@app.post("/davinci/frankenbite")
async def dv_frankenbite(req: DvMontageRequest | None = None):
    req = req or DvMontageRequest()
    transcript = _require_transcript()
    try:
        montages = await extract_montages(
            transcript, max_montages=req.n_videos, min_dur=req.min_dur, max_dur=req.max_dur,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    montages = montages[: req.n_videos]
    # cada montagem inteira ganha 1 cor propria (video montado = 1 cor)
    for i, m in enumerate(montages):
        m["color"] = _DAVINCI_COLORS[i % len(_DAVINCI_COLORS)]
    _dv["montages"] = montages
    _dv_log()
    return {"montages": montages}


@app.post("/davinci/silences")
async def dv_silences():
    transcript = _require_transcript()
    dur = _dv["source"]["duration_sec"] if _dv["source"] else 0.0
    result = remove_silences(transcript, duration_sec=dur)
    _dv["silences"] = {k: v for k, v in result.items() if k != "cuts"}
    _dv_log()
    return result


class DvApplyRequest(BaseModel):
    objective: str            # "viral" | "frankenbite" | "silences"
    ids: list[str] | None = None  # ids de cortes/montagens selecionados


@app.post("/davinci/apply")
async def dv_apply(req: DvApplyRequest):
    src_name = _dv["source"]["name"] if _dv["source"] else "sequencia"
    try:
        if req.objective == "viral":
            sel = [c for c in _dv["viral"] if req.ids is None or c.id in req.ids]
            if not sel:
                raise HTTPException(status_code=400, detail="Nenhum corte selecionado.")
            cuts = [{"start": c.start, "end": c.end} for c in sel]
            colors = [c.color for c in sel]
            # aplica com cores por corte
            result = await asyncio.to_thread(_dv_apply_colored, cuts, colors, f"Cortes Virais — {src_name}")
        elif req.objective == "frankenbite":
            sel = [m for m in _dv["montages"] if req.ids is None or m["id"] in req.ids]
            if not sel:
                raise HTTPException(status_code=400, detail="Nenhuma montagem selecionada.")
            created = 0
            for i, m in enumerate(sel):
                cuts = [{"start": p["start"], "end": p["end"]} for p in m["pieces"]]
                # cada montagem inteira em UMA cor propria (nao mais "Purple" fixo).
                # a cor vem da montagem (atribuida em /davinci/frankenbite), para
                # que preview e timeline batam mesmo com selecao parcial.
                color = m.get("color") or _DAVINCI_COLORS[i % len(_DAVINCI_COLORS)]
                await asyncio.to_thread(davinci.apply_source_cuts, cuts, f"Montagem {i+1} — {src_name}", color)
                created += 1
            result = {"sequences": created}
        elif req.objective == "silences":
            dur = _dv["source"]["duration_sec"] if _dv["source"] else 0.0
            sil = remove_silences(_require_transcript(), duration_sec=dur)
            cuts = [{"start": c["start"], "end": c["end"]} for c in sil["cuts"]]
            result = await asyncio.to_thread(davinci.apply_source_cuts, cuts, f"Sem silencios — {src_name}", "Green")
        else:
            raise HTTPException(status_code=400, detail="Objetivo desconhecido.")
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return result


def _dv_apply_colored(cuts: list, colors: list, name: str) -> dict:
    """apply_source_cuts com uma cor por corte (para falas virais)."""
    import core.adapters.davinci as dv
    mpi, fps, _p, _n, _d = dv._get_main_source()
    resolve = dv._bootstrap()
    project = dv._current_project(resolve)
    media_pool = project.GetMediaPool()
    dv._set_output_folder(media_pool, name)  # pasta por timeline
    clip_infos = []
    for c in cuts:
        # direcional: floor na entrada, ceil na saida -- corte nunca encolhe
        sf, ef = math.floor(c["start"] * fps), math.ceil(c["end"] * fps)
        if ef > sf:
            clip_infos.append({"mediaPoolItem": mpi, "startFrame": sf, "endFrame": ef})
    if not clip_infos:
        raise RuntimeError("Nenhum corte valido.")
    new_tl = media_pool.CreateTimelineFromClips(name, clip_infos)
    if new_tl is None:
        raise RuntimeError("CreateTimelineFromClips retornou None.")
    items = new_tl.GetItemListInTrack("video", 1) or []
    colored = 0
    for i, item in enumerate(items):
        if item.SetClipColor(colors[i] if i < len(colors) else "Blue"):
            colored += 1
    return {"applied": len(items), "colored": colored, "new_timeline_name": name, "warnings": []}
