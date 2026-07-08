"""Configuracao do Core. Le do ambiente (.env). Ver PLANO_MESTRE.md secao 19."""
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    port: int = int(os.getenv("VIRALCUT_PORT", "8756"))
    llm_provider: str = os.getenv("VIRALCUT_LLM", "anthropic")  # anthropic|openai
    llm_model: str = os.getenv("VIRALCUT_LLM_MODEL", "claude-sonnet-5")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    transcribe_engine: str = os.getenv("VIRALCUT_TRANSCRIBE", "api")  # api|whispercpp|whisperx


settings = Settings()
