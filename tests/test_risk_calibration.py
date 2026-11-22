from datetime import datetime, timezone

from app import stellar
from app.config import Settings


ADDRESS = "G" + "A" * 55


def operation(letter: str, amount: str = "1") -> dict:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "type": "payment",
        "source_account": ADDRESS,
        "to": "G" + letter * 55,
        "asset_type": "native",
        "amount": amount,
    }


def score_with(records, *, sequence="100", settings=None, monkeypatch=None):
    # Each fixture represents an independent Horizon response. Avoid reusing a
    # previous result from the process-wide screening cache between tests.
    stellar.clear_screening_cache()
    monkeypatch.setattr(
        stellar,
        "_get",
        lambda url, params=None, settings=None: (
            {"sequence": sequence, "balances": []}
            if url.endswith("/accounts/" + ADDRESS)
            else {"_embedded": {"records": records}}
        ),
    )
    return stellar.score_account(ADDRESS, settings=settings or Settings())


def test_calibration_fixture_empty_account_is_low(monkeypatch):
    result = score_with([], monkeypatch=monkeypatch)

    assert result["score"] == 0
    assert result["risk_level"] == "low"
    assert result["threshold_exceeded"] is False


def test_calibration_fixture_low_sequence_and_counterparty_boundary_is_elevated(monkeypatch):
    records = [operation(letter) for letter in "BCDEFGHIJKLMNOPQRSTU"]
    result = score_with(records, sequence="5", monkeypatch=monkeypatch)

    assert result["metrics"]["distinct_counterparties"] == 20
    assert result["score"] == 40
    assert result["risk_level"] == "elevated"


def test_calibration_fixture_activity_volume_boundary_is_high(monkeypatch):
    records = [operation("B", amount="10000") for _ in range(50)]
    result = score_with(
        records,
        sequence="100",
        settings=Settings(risk_high_score_threshold=40, risk_elevated_score_threshold=20),
        monkeypatch=monkeypatch,
    )

    assert result["score"] == 50
    assert result["risk_level"] == "high"
    assert result["threshold_exceeded"] is True
