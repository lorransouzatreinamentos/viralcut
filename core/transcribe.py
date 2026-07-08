"""
Transcricao word-level (ver PLANO_MESTRE.md secoes 6, 9.1, 16).

Duas engines, tentadas nesta ordem (transparente pro resto do app):
  1. LOCAL (faster-whisper, via local_transcribe.py) -- gratis, offline, privado.
     So roda se o pacote estiver instalado (`pip install faster-whisper`); opcional,
     nao e dependencia obrigatoria. Se nao estiver, cai pra API sem erro visivel.
  2. API (OpenAI Whisper na nuvem) -- sempre funciona se houver OPENAI_API_KEY.

Fluxo cloud: audio bruto -> comprime p/ MP3 16kHz mono (cabe no limite de 25MB
do Whisper p/ a maioria dos videos) -> se ainda exceder, divide em pedacos ->
transcreve cada pedaco -> soma o offset de tempo -> junta tudo num TranscriptState.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

from core.config import settings
from core.model import Segment, TranscriptState, Word

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
CHUNK_LIMIT_BYTES = 24 * 1024 * 1024  # abaixo do limite de 25MB do Whisper (margem de seguranca)

# Script compartilhado com o painel Premiere (Node chama o mesmo arquivo via
# subprocess) -- uma unica implementacao da engine local, ver o proprio arquivo.
_LOCAL_SCRIPT = Path(__file__).resolve().parent.parent / "premiere-panel" / "host" / "local_transcribe.py"


def compress_audio_for_whisper(input_path: str, output_dir: str) -> str:
    """MP3 16kHz mono (~0.5-1MB/min) -- cabe 20-40min num unico upload ao Whisper."""
    output_path = os.path.join(output_dir, Path(input_path).stem + "_compressed.mp3")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ar", "16000", "-ac", "1", "-b:a", "64k", output_path],
            check=True, capture_output=True, timeout=600,
        )
    except FileNotFoundError as e:
        raise RuntimeError("ffmpeg nao encontrado no PATH. Instale com 'brew install ffmpeg'.") from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace")[-500:] if e.stderr else ""
        raise RuntimeError(f"ffmpeg falhou ao comprimir audio: {stderr}") from e
    return output_path


def split_audio_if_needed(path: str, output_dir: str, chunk_seconds: int = 600) -> list[tuple[str, float]]:
    """Se o arquivo exceder o limite do Whisper, divide em pedacos de chunk_seconds.

    Retorna [(caminho_do_pedaco, offset_em_segundos), ...]. offset e o que deve
    ser somado a TODO timestamp retornado pelo Whisper para aquele pedaco
    (ver PLANO_MESTRE.md 16 -- offsets sao relativos ao inicio de cada chunk).
    """
    size = os.path.getsize(path)
    if size <= CHUNK_LIMIT_BYTES:
        return [(path, 0.0)]

    stem = Path(path).stem
    pattern = os.path.join(output_dir, stem + "_chunk%03d.mp3")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-f", "segment", "-segment_time", str(chunk_seconds),
             "-c", "copy", pattern],
            check=True, capture_output=True, timeout=900,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace")[-500:] if e.stderr else ""
        raise RuntimeError(f"ffmpeg falhou ao dividir audio: {stderr}") from e

    chunk_files = sorted(Path(output_dir).glob(stem + "_chunk*.mp3"))
    if not chunk_files:
        raise RuntimeError("ffmpeg nao gerou nenhum pedaco de audio")
    return [(str(f), i * float(chunk_seconds)) for i, f in enumerate(chunk_files)]


def _parse_whisper_response(data: dict, time_offset: float = 0.0) -> tuple[list[Word], list[Segment]]:
    """Converte a resposta verbose_json (word+segment) do Whisper para o modelo canonico.

    Palavras de duracao zero/negativa (raro, mas acontece com pontuacao solta)
    sao descartadas em vez de derrubar a transcricao inteira.
    """
    words: list[Word] = []
    for w in data.get("words", []):
        start = w["start"] + time_offset
        end = w["end"] + time_offset
        if end <= start:
            continue
        words.append(Word(id=len(words), text=str(w.get("word", "")).strip(), start=start, end=end))

    segments: list[Segment] = []
    for seg in data.get("segments", []):
        seg_start = seg["start"] + time_offset
        seg_end = seg["end"] + time_offset
        if seg_end <= seg_start:
            continue
        word_ids = [w.id for w in words if w.start >= seg_start - 0.001 and w.end <= seg_end + 0.001]
        segments.append(Segment(
            id=len(segments), start=seg_start, end=seg_end,
            text=seg.get("text", "").strip(), word_ids=word_ids,
        ))

    return words, segments


async def _call_whisper_api(audio_path: str, language: str) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY nao configurada (.env)")

    with open(audio_path, "rb") as f:
        file_bytes = f.read()

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            WHISPER_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            files={"file": (Path(audio_path).name, file_bytes)},
            data={
                "model": "whisper-1",
                "response_format": "verbose_json",
                "language": language,
                "timestamp_granularities[]": ["word", "segment"],
            },
        )
    resp.raise_for_status()
    return resp.json()


def merge_transcript_states(chunks: list[TranscriptState]) -> TranscriptState:
    """Junta transcricoes de multiplos pedacos, renumerando IDs sequencialmente.

    Os timestamps ja vem com offset aplicado (por _parse_whisper_response);
    aqui so remapeamos IDs para nao colidir entre pedacos.
    """
    all_words: list[Word] = []
    all_segments: list[Segment] = []
    word_id_map: dict[tuple[int, int], int] = {}

    for chunk_idx, chunk in enumerate(chunks):
        for w in chunk.words:
            new_id = len(all_words)
            word_id_map[(chunk_idx, w.id)] = new_id
            all_words.append(Word(id=new_id, text=w.text, start=w.start, end=w.end))

    for chunk_idx, chunk in enumerate(chunks):
        for s in chunk.segments:
            remapped = [word_id_map[(chunk_idx, wid)] for wid in s.word_ids if (chunk_idx, wid) in word_id_map]
            all_segments.append(Segment(
                id=len(all_segments), start=s.start, end=s.end, text=s.text, word_ids=remapped,
            ))

    return TranscriptState(words=all_words, segments=all_segments)


def _try_local_transcribe_sync(audio_path: str, language: str) -> TranscriptState | None:
    """Tenta a engine local (faster-whisper). Retorna None em QUALQUER falha
    (script ausente, pacote nao instalado, erro de inferencia) -- o chamador
    cai para a API sem quebrar a analise. Nunca levanta excecao."""
    if not _LOCAL_SCRIPT.exists():
        return None
    try:
        # sys.executable (nao "python3" fixo) -- garante o MESMO interprete que
        # esta rodando este processo (o venv onde faster-whisper foi instalado).
        # "python3" fixo nao existiria no Windows (venv la so tem python.exe).
        result = subprocess.run(
            [sys.executable, str(_LOCAL_SCRIPT), audio_path, language],
            capture_output=True, text=True, timeout=1800,
        )
    except Exception:  # noqa: BLE001 -- python3 ausente, timeout, etc: fallback silencioso
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if "error" in data:
        return None
    try:
        words = [Word(**w) for w in data.get("words", [])]
        segments = [Segment(**s) for s in data.get("segments", [])]
        return TranscriptState(words=words, segments=segments)
    except Exception:  # noqa: BLE001 -- schema inesperado: fallback
        return None


async def transcribe_timeline_audio(raw_audio_path: str, output_dir: str, language: str = "pt") -> TranscriptState:
    """Orquestra a transcricao: tenta local primeiro (gratis, offline), cai pra
    API se indisponivel. Fluxo da API: comprime -> divide se preciso -> transcreve
    -> junta. VIRALCUT_TRANSCRIBE=api no .env forca sempre a nuvem."""
    if settings.transcribe_engine != "api":
        local = await asyncio.to_thread(_try_local_transcribe_sync, raw_audio_path, language)
        if local is not None and local.segments:
            return local

    compressed = compress_audio_for_whisper(raw_audio_path, output_dir)
    chunks = split_audio_if_needed(compressed, output_dir)

    results = []
    for chunk_path, offset in chunks:
        data = await _call_whisper_api(chunk_path, language)
        words, segments = _parse_whisper_response(data, time_offset=offset)
        results.append(TranscriptState(words=words, segments=segments))

    return results[0] if len(results) == 1 else merge_transcript_states(results)
