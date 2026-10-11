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
    # Location search (Geoapify geocoding autocomplete)
    # ------------------------------------------------------------------
    #: The provider key. Never logged, never returned to a client, and read
    #: only from the environment — there is no default to fall back on, so a
    #: deployment without it degrades to manual location entry instead of
    #: failing to start.
    geoapify_api_key: str = Field(default="", alias="GEOAPIFY_API_KEY")
    #: Overridable so the provider can be pointed at a stub in tests without
    #: patching module globals.
    geoapify_base_url: str = Field(
        default="https://api.geoapify.com/v1/geocode/autocomplete",
        alias="GEOAPIFY_BASE_URL",
    )
    #: Short on purpose: a slow search is worse than no suggestions, and the
    #: user always has the manual fallback.
    geoapify_timeout_seconds: float = Field(
        default=3.0, alias="GEOAPIFY_TIMEOUT_SECONDS"
    )
    #: How long a provider response may be reused. The key includes the query
    #: and the country filter, so a hit is only ever served for the same ask.
    geoapify_cache_ttl_seconds: int = Field(
        default=86400, alias="GEOAPIFY_CACHE_TTL_SECONDS"
    )
    geoapify_enabled: bool = Field(default=True, alias="GEOAPIFY_ENABLED")
    #: Results requested per search. The selector shows at most
    #: ``location_suggestions_shown`` of them.
    geoapify_limit: int = Field(default=8, alias="GEOAPIFY_LIMIT")

    # ------------------------------------------------------------------
    # Location rate limits (per process; see app.services.rate_limit)
    # ------------------------------------------------------------------
    #: Searches per user per window. Typed queries are debounced first, so this
    #: is a backstop against a scripted client rather than a normal-use cap.
    location_search_rate_limit: int = Field(
        default=20, alias="LOCATION_SEARCH_RATE_LIMIT"
    )
    location_search_rate_window: int = Field(
        default=60, alias="LOCATION_SEARCH_RATE_WINDOW"
    )
    #: Manual registrations create durable rows, so the cap is much tighter.
    location_manual_rate_limit: int = Field(
        default=3, alias="LOCATION_MANUAL_RATE_LIMIT"
    )
    location_manual_rate_window: int = Field(
        default=3600, alias="LOCATION_MANUAL_RATE_WINDOW"
    )
    #: REST search, keyed by client address.
    location_api_rate_limit: int = Field(default=60, alias="LOCATION_API_RATE_LIMIT")
    location_api_rate_window: int = Field(default=60, alias="LOCATION_API_RATE_WINDOW")

    # ------------------------------------------------------------------
    # Curated location channel (app.services.location_channel_service)
    # ------------------------------------------------------------------
    #: The channel the administrator maintains the location directory in.
    #: Channel ids are negative (``-100...``). Empty disables the importer
    #: entirely — nothing about the bot depends on it being configured, so a
    #: deployment without it keeps working on the seeded registry.
    location_channel_id: int = Field(default=0, alias="LOCATION_CHANNEL_ID")
    #: Honour ``Status: verified`` in a channel post. The channel is written
    #: only by administrators, so a post is an act of curation; turn this off to
    #: force every imported record through the in-bot review queue instead.
    location_channel_trust_verified: bool = Field(
        default=True, alias="LOCATION_CHANNEL_TRUST_VERIFIED"
    )
    #: Reject a record whose coordinates fall outside Ethiopia. Left on because
    #: the channel is documented as Addis Ababa and an out-of-country
    #: coordinate is always a typo (usually swapped latitude and longitude).
    location_channel_enforce_bounds: bool = Field(
        default=True, alias="LOCATION_CHANNEL_ENFORCE_BOUNDS"
    )
    #: Protect live data from a stale export. When re-running a backfill, a
    #: channel record that a live ``edited_channel_post`` update already
    #: refreshed is left alone, so an old export file can never roll back a
    #: correction made since it was exported. Turn off only if the export is
    #: known to be the newest source.
    location_channel_import_skip_stale: bool = Field(
        default=True, alias="LOCATION_CHANNEL_IMPORT_SKIP_STALE"
    )
    #: Tell an administrator when a channel post is rejected. Off by default
    #: because a public channel attracts unrelated posts, and a stranger
    #: posting "hello" must not be able to make the bot message an admin.
    location_channel_notify_rejections: bool = Field(
        default=False, alias="LOCATION_CHANNEL_NOTIFY_REJECTIONS"
    )

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
    # Job applications ("Apply for This Job")
    # ------------------------------------------------------------------
    #: The bot's own @username, without the ``@``. Used to build the deep link
    #: under a published job post's Apply button, which must open the private
    #: chat — a callback button on a channel post is inert for anyone who has
    #: not started the bot yet. Left empty the bot calls ``getMe`` once and
    #: caches the result, so this is an optimisation rather than a requirement.
    bot_username: str = Field(default="", alias="BOT_USERNAME")

    # ------------------------------------------------------------------
    # Mini App
    # ------------------------------------------------------------------
    mini_app_url: str = Field(default="", alias="MINI_APP_URL")
    #: Signing secret for the web-login JWT (the non-Telegram browser). When it
    #: is empty the web login is disabled rather than signed with a default —
    #: a shared fallback would let anyone mint a token for any Telegram user.
    mini_app_jwt_secret: str = Field(default="", alias="MINI_APP_JWT_SECRET")
    #: How long a one-time login code stays valid, in seconds.
    mini_app_code_ttl_seconds: int = Field(default=600, alias="MINI_APP_CODE_TTL_SECONDS")
    #: How many login codes may be outstanding for one phone number, per hour.
    #: Without this, the codes endpoint is a free SMS/telegram spammable API.
    mini_app_code_rate_limit: int = Field(default=3, alias="MINI_APP_CODE_RATE_LIMIT")
    mini_app_code_rate_window: int = Field(default=3600, alias="MINI_APP_CODE_RATE_WINDOW")
    #: Local development only: returns the one-time code in the API response,
    #: because there is no bot running to deliver it. Leave unset in production.
    mini_app_dev_codes: bool = Field(default=False, alias="MINI_APP_DEV_CODES")

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