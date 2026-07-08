"""
Cache de transcricao por arquivo-fonte.

O app transcreve o ARQUIVO DE VIDEO (nao a timeline). Entao a chave do cache e a
identidade do arquivo: caminho + tamanho + data de modificacao. Se o usuario mexe
na timeline (corta, reordena, adiciona titulos), o arquivo-fonte nao muda e a
transcricao continua valida -- reusar e correto e economiza minutos de CPU.
Se ele troca o video, o fingerprint muda e retranscreve sozinho.

O usuario pode sempre forcar (force=True) -- ex: substituiu o arquivo mantendo o
mesmo nome/tamanho, ou quer trocar o idioma.

Formato do arquivo de cache: o MESMO schema do resto do app (words/segments com
word_ids), para o Node (core-cep.js) ler o cache escrito pelo Python e vice-versa.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

CACHE_DIR = Path.home() / ".viralcut" / "cache"
CACHE_VERSION = 1


def fingerprint(media_path: str, language: str = "pt") -> str | None:
    """Identidade do arquivo. Usa metadados (rapido) em vez de ler o arquivo
    inteiro -- um video de 2GB nao pode custar um hash completo a cada clique."""
    try:
        st = os.stat(media_path)
    except OSError:
        return None
    raw = f"{CACHE_VERSION}|{os.path.abspath(media_path)}|{st.st_size}|{int(st.st_mtime)}|{language}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _path_for(fp: str) -> Path:
    return CACHE_DIR / f"{fp}.json"


def load(media_path: str, language: str = "pt") -> dict | None:
    """Retorna {words, segments, engine, created_at} ou None se nao houver cache."""
    fp = fingerprint(media_path, language)
    if not fp:
        return None
    p = _path_for(fp)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not data.get("segments"):
        return None
    return data


def save(media_path: str, words: list, segments: list, engine: str, language: str = "pt") -> None:
    """Grava o cache. Falha em silencio -- cache e otimizacao, nunca derruba o fluxo."""
    fp = fingerprint(media_path, language)
    if not fp:
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "media_path": os.path.abspath(media_path),
            "language": language,
            "engine": engine,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "words": words,
            "segments": segments,
        }
        _path_for(fp).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def clear(media_path: str, language: str = "pt") -> None:
    fp = fingerprint(media_path, language)
    if not fp:
        return
    try:
        _path_for(fp).unlink(missing_ok=True)
    except OSError:
        pass
