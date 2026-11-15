from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./local.db"
    jwt_secret: str = DEFAULT_JWT_SECRET
    access_token_expire_minutes: int = 60
    bcrypt_rounds: int = 12
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    max_upload_mb: int = 20

    @field_validator("database_url")
    @classmethod
    def normalize_postgres_url(cls, v: str) -> str:
        # Hosting providers hand out postgres:// URLs; SQLAlchemy needs an explicit driver.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @model_validator(mode="after")
    def require_real_secret_in_production(self) -> "Settings":
        if self.app_env == "production" and self.jwt_secret == DEFAULT_JWT_SECRET:
            raise ValueError("JWT_SECRET must be set in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
