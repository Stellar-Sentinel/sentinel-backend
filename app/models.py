"""Public response schemas for the current HTTP API."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RiskSignalResponse(BaseModel):
    id: str
    label: str
    value: Any
    severity: str
    points: int
    explanation: str
    source: str
    window: str


class ScreeningMetricsResponse(BaseModel):
    operations_scanned: int
    operations_in_window: int
    transfers_in_window: int
    transfer_volume_xlm: float
    distinct_counterparties: int
    account_sequence: int
    native_xlm_balance: float
    window_days: int


class ScreeningSourceResponse(BaseModel):
    horizon_url: str
    network: str
    observed_at: str


class ActivitySampleResponse(BaseModel):
    operations_scanned: int
    scan_limit: int
    may_be_incomplete: bool


class ScreeningResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "address": "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "score": 25,
        "risk_level": "low",
        "threshold": 70,
        "threshold_exceeded": False,
        "scoring_policy_version": "1.0.0",
        "activity_sample": {
            "operations_scanned": 12,
            "scan_limit": 200,
            "may_be_incomplete": False,
        },
        "signals": [],
        "metrics": {
            "operations_scanned": 12,
            "operations_in_window": 10,
            "transfers_in_window": 8,
            "transfer_volume_xlm": 120.5,
            "distinct_counterparties": 4,
            "account_sequence": 16,
            "native_xlm_balance": 240.0,
            "window_days": 7,
        },
        "source": {
            "horizon_url": "https://horizon-testnet.stellar.org",
            "network": "Test SDF Network ; September 2015",
            "observed_at": "2026-01-01T12:00:00+00:00",
        },
        "as_of": "2026-01-01T12:00:00+00:00",
        "on_chain_action": "none",
    }]})

    address: str
    score: int = Field(ge=0, le=100)
    risk_level: Literal["low", "elevated", "high"]
    threshold: int
    threshold_exceeded: bool
    scoring_policy_version: str = "1.0.0"
    activity_sample: ActivitySampleResponse | None = None
    signals: list[RiskSignalResponse]
    metrics: ScreeningMetricsResponse
    source: ScreeningSourceResponse
    as_of: str
    on_chain_action: Literal["none"]


class FlagEventResponse(BaseModel):
    id: str | None = None
    ledger: int | None = None
    created_at: str | None = None
    agent: str | None = None
    subject: str | None = None
    score: Any = None
    report_digest: str | None = None
    contract_id: str | None = None
    tx_hash: str | None = None


class EventsSourceResponse(BaseModel):
    rpc_url: str
    network: str
    contract_id: str | None = None
    ingestion_status: str | None = None


class EventsResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "events": [{
            "id": "0000000000000000001-0000000001",
            "ledger": 100,
            "created_at": "2026-01-01T12:00:00Z",
            "agent": "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "subject": "GBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            "score": 82,
            "report_digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "contract_id": "CCAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "tx_hash": "transaction-hash",
        }],
        "next_cursor": None,
        "source": {
            "rpc_url": "https://soroban-testnet.stellar.org",
            "network": "Test SDF Network ; September 2015",
            "contract_id": "CCAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        },
    }]})

    events: list[FlagEventResponse]
    next_cursor: str | None
    source: EventsSourceResponse


class NetworkStatusResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "network": "Test SDF Network ; September 2015",
        "rpc_url": "https://soroban-testnet.stellar.org",
        "status": "healthy",
        "latest_ledger": 100,
        "oldest_ledger": 50,
        "ledger_retention_window": 120960,
        "observed_at": "2026-01-01T12:00:00+00:00",
    }]})

    network: str
    rpc_url: str
    status: str
    latest_ledger: int | None = None
    oldest_ledger: int | None = None
    ledger_retention_window: int | None = None
    observed_at: str
