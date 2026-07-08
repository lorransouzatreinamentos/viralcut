"""Core Engine — servidor local do VIRALCUT (ver PLANO_MESTRE.md secao 8)."""
import asyncio
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


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}


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
        clips = await extract_viral_clips(
            _state["transcript"],
            min_score=req.min_score,
            max_clips=req.max_clips,
            min_dur=req.min_dur,
            max_dur=req.max_dur,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    _state["clips"] = {c.id: c for c in clips}
    return {"clips": [c.model_dump() for c in clips]}


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
