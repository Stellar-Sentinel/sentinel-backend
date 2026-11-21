import pytest
from pydantic import ValidationError

from app.config import Settings


def test_development_keeps_local_defaults_explicitly_allowed():
    settings = Settings(environment="development")

    assert settings.api_key == ""
    assert "localhost" in settings.cors_origins


def test_runtime_validation_rejects_invalid_proxy_networks():
    with pytest.raises(ValueError):
        Settings(trusted_proxy_cidrs="not-a-network")


def test_risk_band_validation_is_applied_during_followup_config_load():
    with pytest.raises(ValidationError, match="RISK_HIGH_SCORE_THRESHOLD"):
        Settings(risk_high_score_threshold=40, risk_elevated_score_threshold=40)
