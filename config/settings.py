"""
Application settings managed via pydantic-settings.

All secrets and tunables are loaded from environment variables (or a .env file)
with strong typing, validation, and sensible defaults.

Railway compatibility:
  - DATABASE_URL       → parsed to override POSTGRES_* settings
  - REDIS_URL          → parsed to override REDIS_* settings
  - PORT               → used as WEBHOOK_PORT
  - RAILWAY_PUBLIC_DOMAIN → used to build WEBHOOK_URL
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ──────────────────────────────────────────────────────────────
# Base directory — resolves to the project root
# ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")



class BotSettings(BaseSettings):
    """Telegram Bot configuration."""

    token: SecretStr
    admin_ids: List[int] = []

    model_config = SettingsConfigDict(env_prefix="BOT_")


class PostgresSettings(BaseSettings):
    """PostgreSQL connection settings."""

    host: str = "localhost"
    port: int = 5432
    user: str = "bot_user"
    password: SecretStr = SecretStr("supersecretpassword")
    db: str = "premium_bot"

    model_config = SettingsConfigDict(env_prefix="POSTGRES_")

    @property
    def async_url(self) -> str:
        """
        Build the async SQLAlchemy connection string.

        Railway provides ``DATABASE_URL`` — if present, convert it from
        ``postgres://`` or ``postgresql://`` to ``postgresql+asyncpg://``.
        """
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            # Railway uses postgres:// or postgresql://, convert for asyncpg
            url = database_url
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+asyncpg://", 1)
            elif url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
        pwd = self.password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.user}:{pwd}"
            f"@{self.host}:{self.port}/{self.db}"
        )

    @property
    def sync_url(self) -> str:
        """
        Build the sync URL (used by Alembic migrations).

        Railway provides ``DATABASE_URL`` — if present, convert it for psycopg2.
        """
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            url = database_url
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+psycopg2://", 1)
            elif url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
            return url
        pwd = self.password.get_secret_value()
        return (
            f"postgresql+psycopg2://{self.user}:{pwd}"
            f"@{self.host}:{self.port}/{self.db}"
        )


class RedisSettings(BaseSettings):
    """Redis connection settings."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: SecretStr | None = None

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    @property
    def url(self) -> str:
        """
        Build the Redis connection string.

        Railway provides ``REDIS_URL`` — use it directly if available.
        """
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            return redis_url
        if self.password and self.password.get_secret_value():
            pwd = self.password.get_secret_value()
            return f"redis://:{pwd}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class WebhookSettings(BaseSettings):
    """
    Webhook configuration (production mode).

    When ``enabled`` is True the bot registers a Telegram webhook
    and starts an aiohttp server instead of long-polling.

    Railway compatibility:
      - ``PORT``                   → overrides ``WEBHOOK_PORT``
      - ``RAILWAY_PUBLIC_DOMAIN``  → auto-builds ``WEBHOOK_URL``
    """

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8443
    url: Optional[str] = None          # e.g. https://yourdomain.com
    path: Optional[str] = None         # auto-set to /webhook/<token>
    secret_token: Optional[str] = None # X-Telegram-Bot-Api-Secret-Token

    model_config = SettingsConfigDict(env_prefix="WEBHOOK_")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Railway injects PORT as the port the app must listen on
        railway_port = os.getenv("PORT")
        if railway_port:
            self.port = int(railway_port)
        # Railway injects RAILWAY_PUBLIC_DOMAIN for the public URL
        railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
        if railway_domain and not self.url:
            self.url = f"https://{railway_domain}"


class Settings(BaseSettings):
    """
    Root settings object.

    Aggregates all sub-settings and exposes application-level tunables.
    Usage:
        from config import settings
        print(settings.bot.token.get_secret_value())
    """

    # ── Sub-settings (populated from env vars automatically) ──
    bot: BotSettings = BotSettings()
    postgres: PostgresSettings = PostgresSettings()
    redis: RedisSettings = RedisSettings()
    webhook: WebhookSettings = WebhookSettings()

    # ── Application-level ─────────────────────────────────────
    debug: bool = False
    logging_level: str = "INFO"
    rate_limit: float = 0.5  # seconds between messages per user

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("logging_level")
    @classmethod
    def _validate_logging_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(
                f"logging_level must be one of {allowed}, got '{v}'"
            )
        return upper


# ──────────────────────────────────────────────────────────────
# Singleton instance — import this everywhere
# ──────────────────────────────────────────────────────────────
settings = Settings()
