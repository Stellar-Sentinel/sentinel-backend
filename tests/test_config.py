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


def test_rejects_invalid_deployment_endpoints_and_contracts():
    with pytest.raises(ValidationError, match="absolute HTTP"):
        Settings(horizon_url="horizon.example")

    with pytest.raises(ValidationError, match="embedded credentials"):
        Settings(soroban_rpc_url="https://user:secret@rpc.example")

    with pytest.raises(ValidationError, match="CONTRACT_ID"):
        Settings(contract_id="not-a-contract")


def test_rejects_enabled_agent_without_complete_signing_configuration():
    with pytest.raises(ValidationError, match="CONTRACT_ID"):
        Settings(agent_enabled=True, agent_secret_key="S" + "A" * 55)

    with pytest.raises(ValidationError, match="AGENT_SECRET_KEY"):
        Settings(agent_enabled=True, contract_id="C" + "A" * 55)


def test_production_requires_secure_complete_deployment():
    valid = {
        "environment": "production",
        "contract_id": "C" + "A" * 55,
        "api_key": "x" * 32,
        "cors_origins": "https://dashboard.example",
    }
    assert Settings(**valid).environment == "production"

    with pytest.raises(ValidationError, match="CONTRACT_ID"):
        Settings(**{key: value for key, value in valid.items() if key != "contract_id"})

    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(**valid, horizon_url="http://horizon.example")


def test_configuration_diagnostics_exclude_secret_values():
    from app.config import configuration_diagnostics

    settings = Settings(api_key="x" * 32, agent_secret_key="S" + "A" * 55)
    diagnostics = configuration_diagnostics(settings)

    assert diagnostics["api_key_configured"] is True
    assert diagnostics["agent_enabled"] is False
    assert "x" * 32 not in str(diagnostics)
    assert "S" + "A" * 55 not in str(diagnostics)


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
