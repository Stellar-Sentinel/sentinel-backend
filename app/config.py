from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    horizon_url: str = "https://horizon-testnet.stellar.org"
    soroban_rpc_url: str = "https://soroban-testnet.stellar.org"
    network_passphrase: str = "Test SDF Network ; September 2015"
    contract_id: str = ""
    environment: str = "development"
    request_timeout_seconds: float = 8.0
    upstream_max_retries: int = Field(default=2, ge=0, le=3)
    upstream_retry_backoff_seconds: float = Field(default=0.2, ge=0, le=1)
    upstream_retry_after_cap_seconds: float = Field(default=2.0, gt=0, le=5)
    operation_scan_limit: int = 200
    activity_window_days: int = 7
    events_lookback_ledgers: int = 50_000
    cors_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
