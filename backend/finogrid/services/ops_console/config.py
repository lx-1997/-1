"""Ops Console configuration."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class OpsConsoleSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )
    database_url: str = "postgresql+asyncpg://finogrid:finogrid@localhost:5432/finogrid"
    ops_api_key: str = "ops_dev_key"       # Ops-level auth; separate from client API keys
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8200
    app_debug: bool = False
    allowed_origins_value: str = Field(
        default="http://localhost:3000", validation_alias="ALLOWED_ORIGINS"
    )

    @property
    def allowed_origins(self) -> list[str]:
        return _parse_csv_list(self.allowed_origins_value)

    def production_validation_errors(self) -> list[str]:
        """Return configuration errors that would make the ops console unsafe."""
        if self.app_debug:
            return ["APP_DEBUG must be false in production"]

        errors: list[str] = []
        origins = self.allowed_origins
        if not origins or "*" in origins:
            errors.append("ALLOWED_ORIGINS must be an explicit origin list in production")
        if self.ops_api_key == "ops_dev_key":
            errors.append("OPS_API_KEY must be changed from the development default")
        database_url = self.database_url.lower()
        if (
            "password@localhost" in database_url
            or "password@127.0.0.1" in database_url
            or "finogrid:finogrid@localhost" in database_url
            or "finogrid:finogrid@127.0.0.1" in database_url
        ):
            errors.append("DATABASE_URL must be changed from the development default")
        return errors
