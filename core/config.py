"""Configuracao do Core. Le do ambiente (.env). Ver PLANO_MESTRE.md secao 19."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Le, em ordem: .env do repo (dev) e ~/.viralcut/.env (onde o instalador grava a
# chave no Windows/Mac). O primeiro que definir a variavel vence.
load_dotenv()
load_dotenv(Path.home() / ".viralcut" / ".env")


class Settings:
    port: int = int(os.getenv("VIRALCUT_PORT", "8756"))
    llm_provider: str = os.getenv("VIRALCUT_LLM", "openai")  # openai|anthropic
    llm_model: str = os.getenv("VIRALCUT_LLM_MODEL", "gpt-4o")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    # Transcricao SEMPRE local (faster-whisper): gratis, offline, privada.
    # "api" e escape hatch manual -- nunca escolhido por fallback automatico.
    transcribe_engine: str = os.getenv("VIRALCUT_TRANSCRIBE", "local")  # local|api


settings = Settings()
