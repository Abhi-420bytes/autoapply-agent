"""Process configuration loaded from the environment / .env.

Only bootstrap values live here (DB URL, master key, API token). Everything the user
edits from the dashboard lives in the database (see app.services.settings_service).
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://autoapply:autoapply@localhost:5432/autoapply"
    redis_url: str = "redis://localhost:6379/0"

    # Fernet key(s) used to encrypt credentials at rest. MASTER_KEY encrypts new data;
    # MASTER_KEY_PREVIOUS (comma-separated) are still accepted for decryption so keys
    # can be rotated without downtime (see `python -m app.cli rotate-keys`).
    master_key: SecretStr
    master_key_previous: SecretStr = SecretStr("")

    # Single-user bearer token protecting the API. The dashboard sends it server-side.
    api_token: SecretStr

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    # Public URL of the dashboard (OAuth redirect URIs are built from it).
    dashboard_url: str = "http://localhost:3000"
    data_dir: str = "/data"
    log_level: str = "INFO"


@lru_cache
def get_config() -> Config:
    return Config()  # required values come from the environment
