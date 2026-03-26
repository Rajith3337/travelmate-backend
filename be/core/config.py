from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)
    database_url: str = "postgresql+asyncpg://postgres.uqjmqjkecvscggvenadj:%40The123datab@aws-1-ap-southeast-2.pooler.supabase.com:5432/postgres"
    secret_key: str = "travelmate-secret-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    upload_dir: str = "uploads"
    gemini_api_key: str = "your-gemini-api-key-here"
    grok_api_key: str = "your-grok-api-key-here"

@lru_cache
def get_settings() -> Settings:
    return Settings()
