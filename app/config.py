from functools import lru_cache
from ipaddress import ip_network
from urllib.parse import urlsplit
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    horizon_url: str = "https://horizon-testnet.stellar.org"
    soroban_rpc_url: str = "https://soroban-testnet.stellar.org"
    network_passphrase: str = "Test SDF Network ; September 2015"
    contract_id: str = ""
    environment: str = "development"
    request_timeout_seconds: float = Field(default=8.0, ge=0.1, le=60)
    upstream_max_retries: int = Field(default=2, ge=0, le=3)
    upstream_retry_backoff_seconds: float = Field(default=0.2, ge=0, le=1)
    upstream_retry_after_cap_seconds: float = Field(default=2.0, gt=0, le=5)
    operation_scan_limit: int = Field(default=200, ge=1, le=1_000)
    activity_window_days: int = Field(default=7, ge=1, le=365)
    screening_cache_ttl_seconds: int = Field(default=15, ge=0, le=300)
    screening_cache_max_entries: int = Field(default=256, ge=1, le=10_000)
    risk_policy_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
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
    events_lookback_ledgers: int = Field(default=50_000, ge=1, le=1_000_000)
    cors_origins: str = "http://localhost:3000"
    api_key: str = ""
    screening_rate_limit_requests: int = Field(default=30, gt=0, le=10_000)
    screening_rate_limit_window_seconds: int = Field(default=60, gt=0, le=86_400)
    trusted_proxy_cidrs: str = ""

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        for origin in origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.path or parsed.query or parsed.fragment
                    or parsed.username or parsed.password):
                raise ValueError(
                    "CORS_ORIGINS must contain exact HTTP(S) origins without paths"
                )
            if "*" in parsed.netloc or "@" in parsed.netloc:
                raise ValueError("CORS_ORIGINS must not contain wildcard origins")
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError("CORS_ORIGINS contains an invalid port") from exc
        return ",".join(origins)

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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def safe_summary(self) -> dict[str, object]:
        """Return deployment diagnostics without secrets or credentialed URLs."""
        return {
            "environment": self.environment,
            "network_passphrase": self.network_passphrase,
            "contract_configured": bool(self.contract_id),
            "api_key_configured": bool(self.api_key),
            "agent_enabled": self.agent_enabled,
            "agent_key_configured": bool(self.agent_secret_key),
            "trusted_proxy_count": len(self.trusted_proxy_networks),
        }
    event_store_path: str = "./data/events.sqlite3"
    # Startup refuses an event store migrated by a newer release. Migrations
    # are additive, so enabling this lets an older release keep reading and
    # writing that file during a deliberate code rollback.
    event_store_allow_newer_schema: bool = False
    event_ingest_interval_seconds: int = Field(default=30, gt=0, le=86_400)
    # Catch-up bounds: one sync reads at most max_pages * page_size events.
    event_ingest_page_size: int = Field(default=100, ge=1, le=1_000)
    event_ingest_max_pages: int = Field(default=25, ge=1, le=100)
    # Wall-clock budget for one sync, checked between pages. The first page is
    # always attempted, so a single slow call can overrun it by one page.
    event_ingest_sync_budget_seconds: float = Field(default=60.0, gt=0, le=3_600)
    # Background polling backs off exponentially after consecutive failures,
    # never sleeping less than the interval nor more than this cap.
    event_ingest_max_backoff_seconds: int = Field(default=300, gt=0, le=86_400)
    # What to do when Soroban RPC rejects the persisted cursor (for example
    # after it ages out of provider retention): "halt" keeps it and reports
    # stale ingestion; "reset" drops it and restarts from the lookback window.
    event_ingest_cursor_recovery: Literal["halt", "reset"] = "halt"
    agent_enabled: bool = False
    agent_secret_key: str = ""
    agent_poll_interval_seconds: int = Field(default=300, ge=30, le=86_400)
    agent_scan_candidate_limit: int = Field(default=20, ge=1, le=200)
    agent_flag_cooldown_seconds: int = Field(default=3600, ge=60, le=604_800)

    @model_validator(mode="after")
    def validate_risk_score_bands(self):
        if self.risk_high_score_threshold <= self.risk_elevated_score_threshold:
            raise ValueError("RISK_HIGH_SCORE_THRESHOLD must exceed RISK_ELEVATED_SCORE_THRESHOLD")
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
