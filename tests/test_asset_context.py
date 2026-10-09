from fastapi.testclient import TestClient

from app import stellar
from app.main import app


client = TestClient(app)
ADDRESS = "G" + "A" * 55


def test_score_returns_distinct_native_and_issued_asset_context(monkeypatch):
    account = {
        "sequence": "10",
        "balances": [
            {"asset_type": "native", "balance": "125.5000000"},
            {
                "asset_type": "credit_alphanum4",
                "asset_code": "USDC",
                "asset_issuer": "G" + "B" * 55,
                "balance": "42.7500000",
            },
            {
                "asset_type": "credit_alphanum12",
                "asset_code": "LONGASSET",
                "asset_issuer": "G" + "C" * 55,
                "balance": "9.25",
            },
        ],
    }

    def fake_get(url, params=None, settings=None):
        if url.endswith("/accounts/" + ADDRESS):
            return account
        return {"_embedded": {"records": []}}

    monkeypatch.setattr(stellar, "_get", fake_get)
    response = client.post("/risk/score", json={"address": ADDRESS})

    assert response.status_code == 200
    result = response.json()
    assert result["metrics"]["native_xlm_balance"] == 125.5
    assert result["metrics"]["trustline_count"] == 2
    assert result["metrics"]["transfer_volume_xlm"] == 0
    assert result["score"] == 0
    assert result["assets"] == [
        {"asset": {"type": "native", "code": "XLM"}, "balance": "125.5000000"},
        {
            "asset": {
                "type": "credit_alphanum4",
                "code": "USDC",
                "issuer": "G" + "B" * 55,
            },
            "balance": "42.7500000",
        },
        {
            "asset": {
                "type": "credit_alphanum12",
                "code": "LONGASSET",
                "issuer": "G" + "C" * 55,
            },
            "balance": "9.25",
        },
    ]


def test_score_handles_empty_and_malformed_asset_records(monkeypatch):
    accounts = [
        {"sequence": "1", "balances": [{"asset_type": "native", "balance": "20"}]},
        {
            "sequence": "1",
            "balances": [
                None,
                {"asset_type": "credit_alphanum4", "asset_code": "BAD", "balance": "3"},
                {
                    "asset_type": "credit_alphanum4",
                    "asset_code": "NAN",
                    "asset_issuer": "G1",
                    "balance": "NaN",
                },
                {"asset_type": "native", "balance": "Infinity"},
            ],
        },
        {"sequence": "1", "balances": None},
    ]

    def fake_get(url, params=None, settings=None):
        if url.endswith("/accounts/" + ADDRESS):
            return accounts.pop(0)
        return {"_embedded": {"records": []}}

    monkeypatch.setattr(stellar, "_get", fake_get)
    with_assets = client.post("/risk/score", json={"address": ADDRESS}).json()
    assert with_assets["metrics"]["trustline_count"] == 0
    assert with_assets["metrics"]["native_xlm_balance"] == 20
    assert with_assets["assets"][0]["asset"]["code"] == "XLM"

    malformed = client.post("/risk/score", json={"address": ADDRESS}).json()
    assert malformed["assets"] == []
    assert malformed["metrics"]["trustline_count"] == 0
    assert malformed["metrics"]["native_xlm_balance"] == 0

    missing = client.post("/risk/score", json={"address": ADDRESS}).json()
    assert missing["assets"] == []
    assert missing["metrics"]["native_xlm_balance"] == 0
