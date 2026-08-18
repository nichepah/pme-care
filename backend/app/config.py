"""Application configuration — all values from environment variables (.env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings with development defaults; production overrides via env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: str = "development"                      # development | demo | production | test
    APP_NAME: str = "PME Care API"
    APP_VERSION: str = "1.1.0"
    API_PREFIX: str = "/api/v1"

    # Neon pooled URL in production; docker-compose Postgres locally.
    DATABASE_URL: str = "postgresql+psycopg://pme:pme@localhost:5432/pme_care"
    DB_POOL_SIZE: int = 5                          # FT-2: small pool per instance

    # A publicly reachable instance holding no real data. Because Firebase web
    # sign-in is not wired yet, such an instance must run with AUTH_FAKE_MODE on
    # — and those tokens are published in this repository, so anyone can sign in
    # as any role. That is acceptable for a demonstration and catastrophic for
    # anything else, so the instance says so on every screen rather than relying
    # on whoever deployed it to remember.
    DEMO_MODE: bool = False

    FIREBASE_PROJECT_ID: str = "pme-care-dev"
    AUTH_FAKE_MODE: bool = False                   # dev/test only: token == uid
    # Where a newly provisioned user lands after following their sign-in link.
    SIGN_IN_CONTINUE_URL: str = "http://localhost:5173/"

    # How long a fitness outcome stays valid, i.e. the PME cycle length. An
    # UNFIT outcome deliberately has no interval — see app/periodicity.py.
    PME_VALIDITY_MONTHS_FIT: int = 12
    PME_VALIDITY_MONTHS_TEMPORARY: int = 3
    # A PME counts as "coming up" this many days before it is due, which is the
    # window the Health Team books from.
    PME_DUE_SOON_DAYS: int = 30

    # Directory of the built frontend, served at "/" when present. Relative to
    # the backend working directory; blank or missing means API-only.
    FRONTEND_DIR: str = "../frontend"

    # Only needed when the UI is served from a *different* origin than the API.
    # Serving both from this process (the default) makes CORS irrelevant.
    ALLOWED_ORIGINS: str = "http://localhost:5173"  # comma-separated
    GCS_BUCKET: str = "pme-care-attachments"

    @property
    def allowed_origins_list(self) -> list[str]:
        """ALLOWED_ORIGINS parsed to a list."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_demo(self) -> bool:
        """True for a throwaway public instance (ENV=demo or DEMO_MODE=true)."""
        return self.DEMO_MODE or self.ENV == "demo"

    @property
    def is_production(self) -> bool:
        """True when ENV=production."""
        return self.ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton (clearable in tests)."""
    return Settings()


settings = get_settings()
