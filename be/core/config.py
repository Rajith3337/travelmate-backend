from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)
    database_url: str = "postgresql+asyncpg://user:password@host:5432/postgres"
    secret_key: str = "change-this-to-a-random-64-char-hex-string"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    gemini_api_key: str = "your-gemini-api-key-here"
    grok_api_key: str = "your-grok-api-key-here"
    supabase_url: str = "https://your-project.supabase.co"
    supabase_service_role_key: str = "your-service-role-key-here"
    supabase_bucket: str = "photos"

@lru_cache
def get_settings() -> Settings:
    return Settings()
