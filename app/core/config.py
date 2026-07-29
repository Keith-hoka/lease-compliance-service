from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration, overridable via environment or .env."""

    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://rental:rental@localhost:5433/lease_compliance"
    api_keys: str = ""
    anthropic_api_key: str = ""
    clause_audit_model: str = "claude-sonnet-5"


settings = Settings()


def clause_audit_enabled() -> bool:
    return bool(settings.anthropic_api_key)
