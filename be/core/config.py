from functools import lru_cache
from pathlib import Path
import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    _default_env = Path(__file__).resolve().parents[1] / ".env"
    _env_file = os.getenv("ENV_FILE") or (str(_default_env) if _default_env.exists() else ".env")
    model_config = SettingsConfigDict(env_file=_env_file, case_sensitive=False)
    database_url: str = "postgresql+asyncpg://<user>:<password>@<host>:5432/<db>?ssl=require"
    secret_key: str = "change-this-to-a-random-64-char-hex-string"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    gemini_api_key: str = "your-gemini-api-key-here"
    grok_api_key: str = "your-grok-api-key-here"
    groq_api_key: str = "your-groq-api-key-here"
    supabase_url: str = "https://your-project.supabase.co"
    supabase_service_role_key: str = "your-service-role-key-here"
    supabase_bucket: str = "photos"

@lru_cache
def get_settings() -> Settings:
    return Settings()