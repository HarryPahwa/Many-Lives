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

    # Room visuals (optional feature; see docs/Room_Visuals_TDD.md).
    # Every one of these is inert while enable_room_visuals is false.
    enable_room_visuals: bool = False
    auto_update_room_visuals: bool = False
    image_client: str = "auto"  # auto | fake | openrouter
    image_model: str = "openai/gpt-image-1-mini"
    image_aspect_ratio: str = "3:2"  # 16:9 is rejected by the default model
    image_quality: str = "low"
    image_output_compression: int = 70
    image_timeout_s: int = 90
    image_edit_include_base: bool = False
    visual_store: str = "auto"  # auto | mongo | file | memory
    visuals_dir: str = ".visuals"
    visual_max_edits: int = 4
    visual_keep_revisions: int = 5

    # Toggles
    use_fake_models: bool = True
    debug_endpoints: bool = False
    balance_file: str = "config/balance.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
