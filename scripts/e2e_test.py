"""Teste end-to-end real: audio -> Whisper -> GPT cortes virais.
Valida o pipeline com APIs reais (nao mocks). Rodar: python scripts/e2e_test.py <audio>"""
import asyncio
import sys
import tempfile

from core.transcribe import transcribe_timeline_audio
from core.viral import extract_viral_clips


async def main(audio_path: str):
    tmp = tempfile.mkdtemp(prefix="viralcut_e2e_")
    print(f"[1/2] Transcrevendo {audio_path} via Whisper…")
    transcript = await transcribe_timeline_audio(audio_path, tmp, language="pt")
    print(f"  -> {len(transcript.words)} palavras, {len(transcript.segments)} segmentos")
    print(f"  primeiras palavras: {' '.join(w.text for w in transcript.words[:12])}…")

    print("\n[2/2] Extraindo cortes virais via GPT…")
    clips = await extract_viral_clips(transcript, min_score=40, max_clips=6, min_dur=8, max_dur=40)
    print(f"  -> {len(clips)} cortes\n")

    for c in clips:
        # Reconstitui o texto do corte a partir das palavras reais (prova que o timecode bate)
        words_in = [w.text for w in transcript.words
                    if c.start <= w.start and w.end <= c.end]
        print(f"  [{c.score}] {c.titulo}  ({c.start:.1f}s–{c.end:.1f}s, {c.end-c.start:.0f}s)")
        print(f"       hook: {c.hook_first_3s}")
        print(f"       texto real no intervalo: \"{' '.join(words_in)}\"")
        # invariante: start/end batem com as palavras referenciadas
        w0 = transcript.word_by_id(c.start_word_id)
        w1 = transcript.word_by_id(c.end_word_id)
        assert abs(c.start - (w0.start - 0.08)) < 0.001 or c.start == 0.0, "start nao deriva da palavra!"
        assert abs(c.end - (w1.end + 0.08)) < 0.001, "end nao deriva da palavra!"
        print()

    print("OK — todos os cortes tem timecode derivado das palavras reais (invariante PLANO 1.1).")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
