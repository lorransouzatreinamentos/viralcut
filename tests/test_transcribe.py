"""
Testes de core/transcribe.py. Nao chamam a API do Whisper de verdade (custaria
dinheiro e precisaria de chave) -- testam o parsing/merge com fixtures, e usam
ffmpeg de verdade (instalado no sistema) para provar que compressao/split funcionam
com arquivos reais, nao mocks.
"""
import shutil
import subprocess

import pytest

from core.transcribe import (
    _parse_whisper_response,
    compress_audio_for_whisper,
    merge_transcript_states,
    split_audio_if_needed,
)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


def _whisper_fixture(offset_words: float = 0.0):
    """Simula uma resposta verbose_json do Whisper para 'todo mundo faz isso errado'."""
    return {
        "words": [
            {"word": "todo", "start": 12.00 + offset_words, "end": 12.20 + offset_words},
            {"word": "mundo", "start": 12.20 + offset_words, "end": 12.50 + offset_words},
            {"word": "faz", "start": 12.50 + offset_words, "end": 12.70 + offset_words},
        ],
        "segments": [
            {"start": 12.00 + offset_words, "end": 12.70 + offset_words, "text": "todo mundo faz"},
        ],
    }


def test_parse_whisper_response_builds_words_and_segments():
    words, segments = _parse_whisper_response(_whisper_fixture())

    assert [w.text for w in words] == ["todo", "mundo", "faz"]
    assert words[0].start == pytest.approx(12.00)
    assert len(segments) == 1
    assert segments[0].word_ids == [0, 1, 2]


def test_parse_whisper_response_applies_time_offset():
    """Fundamental para chunking (secao 16): offset do chunk soma a TODOS os timestamps."""
    words, segments = _parse_whisper_response(_whisper_fixture(), time_offset=600.0)

    assert words[0].start == pytest.approx(612.00)
    assert segments[0].start == pytest.approx(612.00)


def test_parse_whisper_response_skips_zero_duration_words():
    data = {
        "words": [
            {"word": "x", "start": 1.0, "end": 1.0},  # duracao zero -- deve ser descartada
            {"word": "ok", "start": 1.0, "end": 1.3},
        ],
        "segments": [],
    }
    words, _ = _parse_whisper_response(data)
    assert [w.text for w in words] == ["ok"]


def test_merge_transcript_states_renumbers_ids_without_collision():
    words1, segments1 = _parse_whisper_response(_whisper_fixture(offset_words=0.0))
    words2, segments2 = _parse_whisper_response(_whisper_fixture(offset_words=600.0))

    from core.model import TranscriptState
    chunk1 = TranscriptState(words=words1, segments=segments1)
    chunk2 = TranscriptState(words=words2, segments=segments2)

    merged = merge_transcript_states([chunk1, chunk2])

    assert len(merged.words) == 6
    assert [w.id for w in merged.words] == [0, 1, 2, 3, 4, 5]
    # segmento do segundo chunk deve referenciar os IDs remapeados (3,4,5), nao os originais (0,1,2)
    assert merged.segments[1].word_ids == [3, 4, 5]
    assert merged.words[3].start == pytest.approx(612.00)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg nao instalado")
def test_compress_and_split_real_audio(tmp_path):
    """Gera 3s de audio silencioso de verdade e prova que compress/split funcionam
    contra arquivos reais (nao mocks) -- exercita o subprocess ffmpeg de ponta a ponta."""
    raw_wav = tmp_path / "raw.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3", str(raw_wav)],
        check=True, capture_output=True,
    )
    assert raw_wav.exists()

    compressed = compress_audio_for_whisper(str(raw_wav), str(tmp_path))
    assert compressed.endswith(".mp3")
    import os
    assert os.path.exists(compressed)
    assert os.path.getsize(compressed) > 0

    # arquivo pequeno -- split nao deve dividir nada
    chunks = split_audio_if_needed(compressed, str(tmp_path))
    assert len(chunks) == 1
    assert chunks[0][1] == 0.0
