from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()] or ["*"]


class IngressSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://finogrid:password@localhost:5432/finogrid"
    pubsub_project_id: str = "finogrid-local"
    cors_origins_value: str = Field(default="*", validation_alias="CORS_ORIGINS")

    jwt_secret_key: str = "local-dev-secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    @property
    def cors_origins(self) -> list[str]:
        return _parse_csv_list(self.cors_origins_value)


settings = IngressSettings()
