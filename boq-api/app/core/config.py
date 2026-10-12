from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Local runs read boq-management/.env (one level above boq-api/); docker injects env vars directly.
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

_PLACEHOLDER_SECRETS = {"", "change-me", "changeme", "secret"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    ENV: str = "development"
    COMPANY_NAME: str = "AV Group"
    TIMEZONE: str = "Asia/Kolkata"
    PUBLIC_UI_URL: str = "http://localhost:3100"

    DATABASE_URL: str
    REDIS_URL: str | None = None
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 30
    COOKIE_SECURE: bool = False
    REFRESH_COOKIE_NAME: str = "boq_rt"
    CORS_ORIGINS: str = "http://localhost:3100"

    LOGIN_RATE_LIMIT_PER_MIN: int = 5
    LOCKOUT_THRESHOLD: int = 10
    LOCKOUT_MINUTES: int = 15

    SEED_ADMIN_EMAIL: str | None = None
    SEED_ADMIN_PASSWORD: str | None = None
    DEMO_PASSWORD: str | None = None

    GRN_TOLERANCE_PCT: float = 5.0

    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_MB: int = 10

    EMAIL_ENABLED: bool = False

    @field_validator("JWT_SECRET")
    @classmethod
    def _strong_secret(cls, v: str) -> str:
        if v.strip().lower() in _PLACEHOLDER_SECRETS or len(v) < 32:
            raise ValueError("JWT_SECRET must be a random string of at least 32 characters")
        return v

    @property
    def is_production(self) -> bool:
        return self.ENV.lower() == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
