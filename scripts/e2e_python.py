"""E2E do motor Python (DaVinci): transcrever + 3 objetivos. Uso: python scripts/e2e_python.py <audio>"""
import asyncio
import sys
import tempfile

from core.transcribe import transcribe_timeline_audio
from core.viral import extract_viral_clips
from core.objectives import extract_montages, remove_silences


async def main(audio: str):
    tmp = tempfile.mkdtemp()
    print("[1] Transcrevendo…")
    t = await transcribe_timeline_audio(audio, tmp, language="pt")
    print(f"    {len(t.words)} palavras, {len(t.segments)} segmentos")

    print("[2] Falas virais…")
    clips = await extract_viral_clips(t, min_score=40, max_clips=6, min_dur=8, max_dur=40)
    for c in clips:
        print(f"    [{c.score}] {c.titulo} ({c.start:.1f}-{c.end:.1f}s)")

    print("[3] Montar falas…")
    montages = await extract_montages(t, max_montages=3, min_dur=8, max_dur=40)
    for m in montages:
        print(f"    [{m['score']}] {m['titulo']} — {len(m['pieces'])} trechos")

    print("[4] Remover silêncios…")
    sil = remove_silences(t, duration_sec=66.0)
    print(f"    {sil['spans']} falas, {sil['original_sec']:.0f}s -> {sil['new_sec']:.0f}s (economia {sil['saved_sec']:.0f}s)")
    print("\nOK — motor Python (DaVinci) funcional.")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
