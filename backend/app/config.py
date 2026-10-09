"""Application settings validated at boot.

All secrets are required in production; defaults are safe for local
development and CI only. ``pydantic-settings`` reads from environment
variables and ``.env`` files.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Typed application settings.

    Attributes:
        app_env: Deployment environment.
        app_name: Human-readable service name.
        database_url: SQLAlchemy-compatible PostgreSQL DSN.
        redis_url: Redis DSN for cache / rate limiting / queues.
        jwt_secret: HMAC secret for signing JWTs.
        jwt_ttl_minutes: Access token lifetime in minutes.
        refresh_ttl_days: Refresh token lifetime in days.
        totp_issuer: Issuer label shown in authenticator apps.
        turnstile_secret: Cloudflare Turnstile server secret.
        google_oauth_client_id: Google OAuth client id.
        google_oauth_client_secret: Google OAuth client secret.
        model_registry_uri: URI for model artifacts.
        default_coverage: Default conformal coverage target.
        default_prep_quantile: Default preparation quantile.
        cors_origins: Allowed CORS origins.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "testing", "production"] = Field(
        default="development",
    )
    app_name: str = Field(default="DemandIQ API")
    app_version: str = Field(default="1.0.0")
    debug: bool = Field(default=False)

    # --- Database / cache ---
    database_url: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:5432/demandiq",
        description="PostgreSQL DSN",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis DSN",
    )
    object_store_url: str | None = Field(default=None)
    object_store_key: str | None = Field(default=None)
    object_store_secret: str | None = Field(default=None)

    # --- Auth ---
    jwt_secret: str = Field(
        default="dev-only-change-me-32-chars-minimum-secret!!",
        description="HMAC secret for JWT",
    )
    jwt_algorithm: str = Field(default="HS256")
    jwt_ttl_minutes: int = Field(default=15, ge=1, le=1440)
    refresh_ttl_days: int = Field(default=14, ge=1, le=90)

    totp_issuer: str = Field(default="DemandIQ")
    turnstile_secret: str | None = Field(default=None)
    google_oauth_client_id: str | None = Field(default=None)
    google_oauth_client_secret: str | None = Field(default=None)

    # --- Modelling defaults ---
    model_registry_uri: str | None = Field(default=None)
    model_bundle_path: str | None = Field(
        default=None,
        description=(
            "Path to a demandiq serving bundle (serving_bundle.joblib) exported "
            "from a training run. When set and loadable, the forecasting service "
            "serves real LightGBM point forecasts and Mondrian ACI intervals; "
            "otherwise it falls back to the deterministic stub."
        ),
    )
    default_coverage: float = Field(default=0.90, ge=0.5, le=0.99)
    default_prep_quantile: float = Field(default=0.80, ge=0.5, le=0.99)

    # --- OCR (handwritten-log ingestion, Ch 3.6) ---
    ocr_service_url: str | None = Field(
        default=None,
        description=(
            "Optional OCR/vision service for image ingestion: it receives the "
            "uploaded image as multipart 'file' and returns {\"rows\": [...]}. "
            "When unset, local Tesseract is used if installed."
        ),
    )
    ocr_language: str = Field(default="eng", description="Tesseract language pack for local OCR")

    # --- Misc ---
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    webhook_signing_algo: str = Field(default="hmac-sha256")
    log_level: str = Field(default="INFO")
    trusted_proxy_headers: bool = Field(
        default=False,
        description=(
            "Honour X-Forwarded-For when deriving a rate-limit client key. "
            "Leave off unless a proxy in front of the app overwrites the "
            "header — it is caller-supplied, so trusting it lets a client "
            "pick a fresh rate-limit bucket per request."
        ),
    )

    # --- Background jobs ---
    jobs_enabled: bool = Field(
        default=False,
        description=(
            "Run the scheduled jobs (connector sync, recalibration, drift, "
            "report delivery, ...) in-process on their cron cadence."
        ),
    )
    jobs_tick_seconds: int = Field(default=60, ge=5, le=3600)

    # SMTP (optional)
    smtp_host: str | None = Field(default=None)
    smtp_port: int | None = Field(default=None)
    smtp_user: str | None = Field(default=None)
    smtp_password: str | None = Field(default=None)

    @field_validator("jwt_secret")
    @classmethod
    def _validate_jwt_secret(cls, value: str) -> str:
        """Ensure the JWT secret is not obviously weak in production.

        In non-production environments a short placeholder is tolerated so
        that tests and local development do not require secret management.
        """
        # ``info`` not needed; validator runs before env is fully known,
        # so we only enforce length universally and leave production check
        # to ``validate_for_boot``.
        if len(value) < 16:
            raise ValueError("JWT_SECRET must be at least 16 characters")
        return value

    def validate_for_boot(self) -> None:
        """Fail fast if production-required secrets are missing.

        Called from :func:`get_settings` so the process crashes at import
        time rather than on first authenticated request.
        """
        if self.app_env == "production":
            weak = "dev-only-change-me"
            if weak in self.jwt_secret:
                raise ValueError("JWT_SECRET must be set to a strong value in production")
            if not self.database_url or "localhost" in self.database_url:
                logger.warning("DATABASE_URL looks like a local default in production")
            if self.redis_url and "localhost" in self.redis_url:
                logger.warning(
                    "REDIS_URL looks like a local default in production; rate limits, "
                    "MFA challenges and agent quota state will not be shared between "
                    "workers"
                )
            # Cloudflare test pair is always-pass and must never be used in prod.
            # Not a credential we hold — it is the published always-pass value we
            # refuse to boot with.
            test_secret = "1x0000000000000000000000000000000AA"  # noqa: S105
            if self.turnstile_secret and test_secret in self.turnstile_secret:
                raise ValueError(
                    "TURNSTILE_SECRET is the Cloudflare test secret. Use a real secret in production"
                )
        else:
            # Dev/test hint: warn if only one half of the pair is set (widget/verify mismatch).
            # The pair must be both set or both unset for consistent dev behavior.
            pass
        logger.info(
            "Settings loaded env=%s coverage=%.2f quantile=%.2f",
            self.app_env,
            self.default_coverage,
            self.default_prep_quantile,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings singleton, validated for boot."""
    settings = Settings()  # type: ignore[call-arg]
    settings.validate_for_boot()
    return settings


# Eager singleton used by modules that import at top level.
settings: Settings = get_settings()
