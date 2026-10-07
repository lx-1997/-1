from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class AgentLedgerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    app_env: str = "development"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8100
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://finogrid:password@localhost:5432/finogrid"
    cors_origins_value: str = Field(default="http://localhost:3000", validation_alias="CORS_ORIGINS")

    # Chain (Base L2 default — aligns with x402 standard)
    chain: str = "base"
    base_rpc_url: str = "https://mainnet.base.org"
    chain_enabled: bool = False  # Set True in prod; False skips on-chain calls
    chain_min_confirmations: int = 12

    # Finogrid deposit address on Base (USDC top-ups arrive here)
    agent_ledger_deposit_address: str = "0x0000000000000000000000000000000000000000"

    # Sweep wallet (private key in GCP Secret Manager — never in DB)
    sweep_wallet_address: str = "0x0000000000000000000000000000000000000000"

    # KYA thresholds (USDC/day)
    kya_basic_daily_limit_usdc: float = 1.00
    kya_enhanced_daily_limit_usdc: float = 100.00
    kya_enabled: bool = True  # Set False in test environments

    # KYA validator MCP server
    kya_validator_mcp_url: str = "http://localhost:9005"

    # Wallet factory MCP server
    wallet_factory_mcp_url: str = "http://localhost:9004"

    # v1 Ingress API (for withdrawal routing)
    v1_ingress_url: str = "http://localhost:8000"
    v1_internal_api_key: str = "internal-service-key"

    # x402 settings
    x402_payment_protected_paths_value: str = Field(default="", validation_alias="X402_PAYMENT_PROTECTED_PATHS")
    x402_nonce_ttl_seconds: int = 300
    x402_max_future_skew_seconds: int = 30
    x402_onchain_verifier_enabled: bool = False
    x402_rpc_timeout_seconds: int = 10

    # Intent sweeper
    intent_sweeper_interval_seconds: int = 300

    # Chain watcher
    chain_watcher_sweep_interval_seconds: int = 60
    usdc_contract_address_base: str = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

    @property
    def x402_payment_protected_paths(self) -> list[str]:
        return _parse_csv_list(self.x402_payment_protected_paths_value)

    @property
    def cors_origins(self) -> list[str]:
        return _parse_csv_list(self.cors_origins_value)

    def production_validation_errors(self) -> list[str]:
        """Return fail-closed configuration errors for a real-money deployment."""
        if self.app_env.lower() not in {"production", "prod"}:
            return []
        errors: list[str] = []
        if self.app_debug:
            errors.append("APP_DEBUG must be false in production")
        if not self.cors_origins or "*" in self.cors_origins:
            errors.append("CORS_ORIGINS must be an explicit allow-list in production")
        if "password@localhost" in self.database_url or "password@127.0.0.1" in self.database_url:
            errors.append("DATABASE_URL still uses the development placeholder")
        if self.chain_enabled:
            if not self.base_rpc_url.startswith("https://"):
                errors.append("BASE_RPC_URL must use HTTPS when chain settlement is enabled")
            zero = "0x0000000000000000000000000000000000000000"
            if self.agent_ledger_deposit_address.lower() == zero or self.sweep_wallet_address.lower() == zero:
                errors.append("deposit and sweep wallet addresses must be configured before enabling chain settlement")
            if self.chain_min_confirmations < 1:
                errors.append("CHAIN_MIN_CONFIRMATIONS must be at least 1")
        if self.x402_payment_protected_paths:
            if not self.x402_onchain_verifier_enabled:
                errors.append("x402 protected paths require an on-chain receipt verifier")
            if not self.chain_enabled:
                errors.append("x402 protected paths require CHAIN_ENABLED=true")
        if self.v1_internal_api_key == "internal-service-key":
            errors.append("V1_INTERNAL_API_KEY still uses the development placeholder")
        return errors


settings = AgentLedgerSettings()
