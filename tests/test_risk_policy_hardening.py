import math

import pytest

from app import stellar
from app.config import Settings


def test_policy_rejects_invalid_threshold_order():
    with pytest.raises(ValueError, match="RISK_HIGH_SCORE_THRESHOLD"):
        Settings(risk_high_score_threshold=40, risk_elevated_score_threshold=40)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -1])
def test_screening_ignores_invalid_transfer_amounts(value):
    assert stellar._finite_float(value) == 0.0


def test_policy_version_is_explicit_in_screening_output(monkeypatch):
    address = "G" + "A" * 55
    monkeypatch.setattr(
        stellar,
        "_get",
        lambda url, params=None, settings=None: (
            {"sequence": "10", "balances": []}
            if url.endswith("/accounts/" + address)
            else {"_embedded": {"records": []}}
        ),
    )

    result = stellar.score_account(address, settings=Settings(risk_policy_version="3.1.4"))

    assert result["scoring_policy_version"] == "3.1.4"
    assert result["score"] == 0
