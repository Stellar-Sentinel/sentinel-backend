from functools import lru_cache
from ipaddress import ip_network
from urllib.parse import urlsplit
import re

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_STELLAR_CONTRACT_ID = re.compile(r"^C[A-Z2-7]{55}$")
_STELLAR_SECRET_KEY = re.compile(r"^S[A-Z2-7]{55}$")
_URL_FIELDS = ("horizon_url", "soroban_rpc_url")


def _validate_endpoint(value: str, field_name: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not contain embedded credentials")
    return value.rstrip("/")


def configuration_diagnostics(settings: "Settings") -> dict[str, object]:
    """Return safe startup diagnostics without exposing configuration secrets."""
    return {
        "environment": settings.environment,
        "network_passphrase": settings.network_passphrase,
        "horizon_origin": _origin(settings.horizon_url),
        "soroban_rpc_origin": _origin(settings.soroban_rpc_url),
        "contract_configured": bool(settings.contract_id),
        "agent_enabled": settings.agent_enabled,
        "api_key_configured": bool(settings.api_key),
    }


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}"


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

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_trusted_proxy_cidrs(cls, value: str) -> str:
        for item in value.split(","):
            item = item.strip()
            if item:
                ip_network(item, strict=False)
        return value

    @field_validator("horizon_url", "soroban_rpc_url")
    @classmethod
    def validate_endpoint_url(cls, value: str, info) -> str:
        return _validate_endpoint(value, info.field_name)

    @property
    def trusted_proxy_networks(self):
        return [ip_network(item.strip(), strict=False)
                for item in self.trusted_proxy_cidrs.split(",") if item.strip()]
    event_store_path: str = "./data/events.sqlite3"
    event_ingest_interval_seconds: int = Field(default=30, gt=0, le=86_400)
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

    @model_validator(mode="after")
    def validate_deployment_contract(self):
        environment = self.environment.strip().lower()
        if not environment:
            raise ValueError("ENVIRONMENT must not be blank")
        if not self.network_passphrase.strip():
            raise ValueError("NETWORK_PASSPHRASE must not be blank")
        if self.contract_id and not _STELLAR_CONTRACT_ID.fullmatch(self.contract_id):
            raise ValueError("CONTRACT_ID must be a valid Stellar contract ID")
        if self.agent_enabled:
            if not self.contract_id:
                raise ValueError("AGENT_ENABLED requires CONTRACT_ID")
            if not self.agent_secret_key:
                raise ValueError("AGENT_ENABLED requires AGENT_SECRET_KEY")
            if not _STELLAR_SECRET_KEY.fullmatch(self.agent_secret_key):
                raise ValueError("AGENT_SECRET_KEY must be a valid Stellar secret key")
        if environment == "production":
            if not self.contract_id:
                raise ValueError("production deployments require CONTRACT_ID")
            if len(self.api_key) < 32:
                raise ValueError("production deployments require an API key of at least 32 characters")
            if any(urlsplit(getattr(self, field)).scheme != "https" for field in _URL_FIELDS):
                raise ValueError("production upstream URLs must use HTTPS")
            origins = [origin.strip().lower() for origin in self.cors_origins.split(",") if origin.strip()]
            if any("localhost" in origin or "127.0.0.1" in origin for origin in origins):
                raise ValueError("production CORS origins must not include local development hosts")
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
