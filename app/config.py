"""Application configuration loaded from the environment / `.env` file."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


class Settings(BaseSettings):
    """All runtime configuration values.

    Every value can be supplied through environment variables (or a `.env`
    file).  No credential is ever hardcoded in source.
    """

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = Field(default="", alias="BOT_TOKEN")

    admin_user_ids: list[int] = Field(default_factory=list, alias="ADMIN_USER_IDS")
    tutor_storage_chat_id: int = Field(default=0, alias="TUTOR_STORAGE_CHAT_ID")
    student_request_chat_id: int = Field(default=0, alias="STUDENT_REQUEST_CHAT_ID")
    support_chat_id: int = Field(default=0, alias="SUPPORT_CHAT_ID")
    job_post_chat_id: int = Field(default=0, alias="JOB_POST_CHAT_ID")

    api_secret: str = Field(default="", alias="API_SECRET")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_cors_origins: list[str] = Field(default_factory=list, alias="API_CORS_ORIGINS")

    database_url: str = Field(default="sqlite:///data/tutor_index.db", alias="DATABASE_URL")

    website_url: str = Field(default="https://tedortutors.com", alias="WEBSITE_URL")
    etb_per_usd: float = Field(default=130.0, alias="ETB_PER_USD")

    storage_max_attempts: int = Field(default=3, alias="STORAGE_MAX_ATTEMPTS")
    storage_backoff_seconds: float = Field(default=1.0, alias="STORAGE_BACKOFF_SECONDS")

    # ------------------------------------------------------------------
    # Tutor-job matching
    # ------------------------------------------------------------------
    #: How many candidates a recommendation report shows (Requirement 17).
    match_top_n: int = Field(default=5, alias="MATCH_TOP_N")
    #: Score (0-100) a newly verified tutor must reach before the admin is
    #: notified about a strong match for an open job (Requirement 22).
    match_notification_threshold: float = Field(
        default=85.0, alias="MATCH_NOTIFICATION_THRESHOLD"
    )
    #: Candidates scoring below this are not persisted or reported.
    match_min_score: float = Field(default=50.0, alias="MATCH_MIN_SCORE")

    # ------------------------------------------------------------------
    # Data validation (Requirement 23)
    # ------------------------------------------------------------------
    tutor_min_age: int = Field(default=18, alias="TUTOR_MIN_AGE")
    tutor_max_age: int = Field(default=100, alias="TUTOR_MAX_AGE")
    english_proficiency_min: int = Field(default=1, alias="ENGLISH_PROFICIENCY_MIN")
    english_proficiency_max: int = Field(default=10, alias="ENGLISH_PROFICIENCY_MAX")
    #: Upper bound of the configured CGPA grading system.
    cgpa_max: float = Field(default=4.0, alias="CGPA_MAX")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator(
        "admin_user_ids",
        "api_cors_origins",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
            return [part for part in parts if part]
        return value

    @field_validator("admin_user_ids", mode="after")
    @classmethod
    def _coerce_ints(cls, value: list[int]) -> list[int]:
        return [int(item) for item in value]

    @field_validator("api_cors_origins", mode="after")
    @classmethod
    def _drop_wildcard_origins(cls, value: list[str]) -> list[str]:
        return [item for item in value if item]

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def ensure_data_dir(self) -> None:
        """Create the SQLite data directory when a file based DB is used."""
        if not self.is_sqlite:
            return
        raw = self.database_url.split("sqlite:///")[-1]
        if not raw or raw == ":memory:" or raw.startswith(":"):
            return
        path = Path(raw)
        if not path.is_absolute():
            path = BASE_DIR / path
        os.makedirs(path.parent, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def reload_settings() -> Settings:
    """Clear the cached settings instance (used by tests)."""
    get_settings.cache_clear()
    return get_settings()