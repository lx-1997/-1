"""Ops Console configuration."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] or ["*"]


class OpsConsoleSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://finogrid:finogrid@localhost:5432/finogrid"
    ops_api_key: str = "ops_dev_key"       # Ops-level auth; separate from client API keys
    app_host: str = "0.0.0.0"
    app_port: int = 8200
    app_debug: bool = True
    allowed_origins_value: str = Field(default="*", validation_alias="ALLOWED_ORIGINS")

    @property
    def allowed_origins(self) -> list[str]:
        return _parse_csv_list(self.allowed_origins_value)
