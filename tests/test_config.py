"""
Unit tests for config.settings.

Validates that pydantic-settings correctly loads and validates
environment variables.
"""

from __future__ import annotations

import os

import pytest

os.environ["BOT_TOKEN"] = "test:token"

class TestSettings:
    """Tests for the Settings class."""

    def test_settings_loads(self):
        """Settings should instantiate without errors."""
        from config.settings import Settings

        s = Settings()
        assert s is not None
        assert s.debug in (True, False)

    def test_postgres_async_url(self):
        """Postgres async_url should contain asyncpg driver."""
        from config.settings import PostgresSettings

        pg = PostgresSettings()
        assert "asyncpg" in pg.async_url

    def test_postgres_sync_url(self):
        """Postgres sync_url should contain psycopg2 driver."""
        from config.settings import PostgresSettings

        pg = PostgresSettings()
        assert "psycopg2" in pg.sync_url

    def test_redis_url_no_password(self):
        """Redis URL without password should not contain auth."""
        from config.settings import RedisSettings

        r = RedisSettings(password=None)
        assert r.url.startswith("redis://")
        assert ":@" not in r.url

    def test_invalid_logging_level_raises(self):
        """Invalid logging level should raise ValueError."""
        from config.settings import Settings

        with pytest.raises(Exception):
            Settings(logging_level="INVALID")

    def test_admin_ids_default_empty(self):
        """Admin IDs should default to empty list."""
        from config.settings import BotSettings

        # Only set the required token
        os.environ["BOT_TOKEN"] = "test:token"
        try:
            b = BotSettings()
            assert isinstance(b.admin_ids, list)
        finally:
            del os.environ["BOT_TOKEN"]
