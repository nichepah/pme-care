"""Application configuration — all values from environment variables (.env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings with development defaults; production overrides via env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: str = "development"                      # development | production | test
    APP_NAME: str = "PME Care API"
    APP_VERSION: str = "1.1.0"
    API_PREFIX: str = "/api/v1"

    # Neon pooled URL in production; docker-compose Postgres locally.
    DATABASE_URL: str = "postgresql+psycopg://pme:pme@localhost:5432/pme_care"
    DB_POOL_SIZE: int = 5                          # FT-2: small pool per instance

    FIREBASE_PROJECT_ID: str = "pme-care-dev"
    AUTH_FAKE_MODE: bool = False                   # dev/test only: token == uid
    # Where a newly provisioned user lands after following their sign-in link.
    SIGN_IN_CONTINUE_URL: str = "http://localhost:5173/"

    ALLOWED_ORIGINS: str = "http://localhost:5173"  # comma-separated
    GCS_BUCKET: str = "pme-care-attachments"

    @property
    def allowed_origins_list(self) -> list[str]:
        """ALLOWED_ORIGINS parsed to a list."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        """True when ENV=production."""
        return self.ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton (clearable in tests)."""
    return Settings()


settings = get_settings()
