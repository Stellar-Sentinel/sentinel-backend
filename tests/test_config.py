import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_timeout_seconds", 0),
        ("request_timeout_seconds", 61),
        ("operation_scan_limit", 0),
        ("operation_scan_limit", 1_001),
        ("activity_window_days", 0),
        ("activity_window_days", 366),
        ("events_lookback_ledgers", 0),
        ("events_lookback_ledgers", 1_000_001),
    ],
)
def test_rejects_unsafe_runtime_limits(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_accepts_runtime_limit_boundaries():
    settings = Settings(
        request_timeout_seconds=0.1,
        operation_scan_limit=1_000,
        activity_window_days=365,
        events_lookback_ledgers=1_000_000,
    )

    assert settings.request_timeout_seconds == 0.1
    assert settings.operation_scan_limit == 1_000
    assert settings.activity_window_days == 365
    assert settings.events_lookback_ledgers == 1_000_000


def test_production_requires_secure_configuration():
    with pytest.raises(ValidationError, match="API key"):
        Settings(environment="production", contract_id="contract", cors_origins="https://app.example")

    with pytest.raises(ValidationError, match="CONTRACT_ID"):
        Settings(environment="production", api_key="x" * 32, cors_origins="https://app.example")

    with pytest.raises(ValidationError, match="CORS"):
        Settings(
            environment="production",
            contract_id="contract",
            api_key="x" * 32,
            cors_origins="*",
        )

    assert Settings(
        environment="production",
        contract_id="contract",
        api_key="x" * 32,
        cors_origins="https://app.example",
    ).environment == "production"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"horizon_url": "stellar.example"},
        {"soroban_rpc_url": "https://user:password@rpc.example"},
    ],
)
def test_rejects_unsafe_upstream_urls(kwargs):
    with pytest.raises(ValidationError, match="upstream URLs"):
        Settings(**kwargs)


@pytest.mark.parametrize("kwargs", [{"environment": " "}, {"network_passphrase": "\t"}])
def test_rejects_blank_deployment_labels(kwargs):
    with pytest.raises(ValidationError, match="deployment labels"):
        Settings(**kwargs)


def test_agent_requires_signing_configuration():
    with pytest.raises(ValidationError, match="AGENT_ENABLED"):
        Settings(agent_enabled=True, contract_id="contract")


def test_deployment_diagnostics_redact_secret_values():
    settings = Settings(api_key="a" * 32, agent_secret_key="S" * 56)

    diagnostics = settings.deployment_diagnostics()

    assert diagnostics["api_key_configured"] is True
    assert diagnostics["agent_secret_configured"] is True
    assert "a" * 32 not in str(diagnostics)
    assert "S" * 56 not in str(diagnostics)


def test_cors_origins_are_exact_http_origins():
    settings = Settings(cors_origins="https://dashboard.example, http://localhost:3000")

    assert settings.cors_origin_list == [
        "https://dashboard.example",
        "http://localhost:3000",
    ]

    for origin in (
        "*",
        "https://*.example.com",
        "https://dashboard.example/path",
        "dashboard.example",
        "ftp://dashboard.example",
        "https://dashboard.example:invalid",
    ):
        with pytest.raises(ValidationError):
            Settings(cors_origins=origin)
