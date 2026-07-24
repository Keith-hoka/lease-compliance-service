from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration, overridable via environment or .env."""

    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://rental:rental@localhost:5433/lease_compliance"
    api_keys: str = ""


settings = Settings()
