from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    horizon_url: str = "https://horizon-testnet.stellar.org"
    soroban_rpc_url: str = "https://soroban-testnet.stellar.org"
    network_passphrase: str = "Test SDF Network ; September 2015"
    contract_id: str = ""
    environment: str = "development"
    request_timeout_seconds: float = Field(default=8.0, ge=0.1, le=60)
    operation_scan_limit: int = Field(default=200, ge=1, le=1_000)
    activity_window_days: int = Field(default=7, ge=1, le=365)
    events_lookback_ledgers: int = Field(default=50_000, ge=1, le=1_000_000)
    cors_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
