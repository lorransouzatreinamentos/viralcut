#!/usr/bin/env python3
"""Transcricao LOCAL via faster-whisper (opcional, sem custo por minuto).

Arquivo unico usado pelos dois hosts:
  - Python/DaVinci (core/transcribe.py) importa/chama este script via subprocess
  - Node/Premiere (core-cep.js) chama via child_process -- por isso fica aqui
    dentro de premiere-panel/host/, que e copiado integralmente pelo instalador
    (nao precisa de passo extra no install-premiere.sh / install-windows.ps1).

Se faster-whisper nao estiver instalado, imprime {"error": "..."} e sai com
codigo 1 -- o chamador (Python ou Node) mostra a instrucao de instalacao. NAO ha
fallback pra nuvem: a transcricao roda sempre aqui, no computador do usuario.

Uso: python local_transcribe.py <audio> [idioma] [modelo]
Saida (stdout): JSON {"words": [...], "segments": [...]}  -- mesmo schema
usado no resto do app (word_ids fazem a ponte entre os dois).
"""
import json
import sys


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "uso: local_transcribe.py <audio> [idioma] [modelo]"}))
        sys.exit(1)

    audio_path = sys.argv[1]
    language = sys.argv[2] if len(sys.argv) > 2 else "pt"
    model_size = sys.argv[3] if len(sys.argv) > 3 else "small"

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(json.dumps({"error": "faster-whisper nao instalado (pip install faster-whisper)"}))
        sys.exit(1)

    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments_gen, _info = model.transcribe(
            audio_path, language=language, word_timestamps=True, vad_filter=True
        )

        words = []
        segments = []
        for seg in segments_gen:
            word_ids = []
            if seg.words:
                for w in seg.words:
                    wid = len(words)
                    text = (w.word or "").strip()
                    if not text or w.end <= w.start:
                        continue
                    words.append({"id": wid, "text": text, "start": w.start, "end": w.end})
                    word_ids.append(wid)
            seg_text = (seg.text or "").strip()
            if not seg_text or not word_ids:
                continue
            segments.append({
                "id": len(segments), "start": seg.start, "end": seg.end,
                "text": seg_text, "word_ids": word_ids,
            })

        print(json.dumps({"words": words, "segments": segments}))
    except Exception as e:  # noqa: BLE001 -- erro estruturado p/ o chamador exibir
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
