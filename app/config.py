"""Settings and balance loading (TDD §19).

Reads environment variables (or an untracked .env). Secrets are never
committed. See .env.example for the full list.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # MongoDB
    mongodb_uri: str = ""
    mongodb_db: str = "dungeon"

    # Model + embedding access
    openrouter_api_key: str = ""
    model_dresser: str = ""
    model_adjudicator: str = ""
    model_narrator: str = ""
    model_verifier: str = ""
    embedding_model: str = ""
    embedding_dims: int = 1536

    # Toggles
    use_fake_models: bool = True
    debug_endpoints: bool = False
    balance_file: str = "config/balance.yaml"

    # Speech. Empty key means narration stays text-only.
    elevenlabs_api_key: str = ""
    # George, a premade narrative voice. Override with any voice id.
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
    elevenlabs_model_id: str = "eleven_flash_v2_5"


@lru_cache
def get_settings() -> Settings:
    return Settings()
