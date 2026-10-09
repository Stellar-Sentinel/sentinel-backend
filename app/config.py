from functools import lru_cache
from ipaddress import ip_network

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    horizon_url: str = "https://horizon-testnet.stellar.org"
    soroban_rpc_url: str = "https://soroban-testnet.stellar.org"
    network_passphrase: str = "Test SDF Network ; September 2015"
    contract_id: str = ""
    environment: str = "development"
    request_timeout_seconds: float = 8.0
    operation_scan_limit: int = 200
    activity_window_days: int = 7
    risk_activity_burst_min_operations: int = Field(default=50, ge=1, le=2_000)
    risk_activity_burst_points: int = Field(default=25, ge=0, le=100)
    risk_transfer_volume_xlm_threshold: float = Field(default=10_000, gt=0, le=1_000_000_000)
    risk_transfer_volume_points: int = Field(default=25, ge=0, le=100)
    risk_counterparty_min_count: int = Field(default=20, ge=1, le=2_000)
    risk_counterparty_points: int = Field(default=25, ge=0, le=100)
    risk_low_sequence_max: int = Field(default=5, ge=0, le=1_000_000)
    risk_low_sequence_points: int = Field(default=15, ge=0, le=100)
    risk_high_score_threshold: int = Field(default=70, ge=1, le=100)
    risk_elevated_score_threshold: int = Field(default=40, ge=0, le=99)
    events_lookback_ledgers: int = 50_000
    cors_origins: str = "http://localhost:3000"
    screening_rate_limit_requests: int = Field(default=30, gt=0, le=10_000)
    screening_rate_limit_window_seconds: int = Field(default=60, gt=0, le=86_400)
    trusted_proxy_cidrs: str = ""

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_trusted_proxy_cidrs(cls, value: str) -> str:
        for item in value.split(","):
            item = item.strip()
            if item:
                ip_network(item, strict=False)
        return value

    @property
    def trusted_proxy_networks(self):
        return [ip_network(item.strip(), strict=False)
                for item in self.trusted_proxy_cidrs.split(",") if item.strip()]
    event_store_path: str = "./data/events.sqlite3"
    event_ingest_interval_seconds: int = Field(default=30, gt=0, le=86_400)

    @model_validator(mode="after")
    def validate_risk_score_bands(self):
        if self.risk_high_score_threshold <= self.risk_elevated_score_threshold:
            raise ValueError("RISK_HIGH_SCORE_THRESHOLD must exceed RISK_ELEVATED_SCORE_THRESHOLD")
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
