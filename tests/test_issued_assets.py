from fastapi.testclient import TestClient

from app import stellar
from app.main import app


ADDRESS = "G" + "A" * 55


def test_issued_assets_keep_same_code_separate_by_issuer(monkeypatch):
    monkeypatch.setattr(stellar, "_get", lambda url, **kwargs: {
        "sequence": "1",
        "balances": [
            {"asset_type": "credit_alphanum4", "asset_code": "USD", "asset_issuer": "G" + "B" * 55, "balance": "1"},
            {"asset_type": "credit_alphanum4", "asset_code": "USD", "asset_issuer": "G" + "C" * 55, "balance": "2"},
        ],
    } if url.endswith(ADDRESS) else {"_embedded": {"records": []}})

    response = TestClient(app).post("/risk/score", json={"address": ADDRESS})

    assert response.status_code == 200
    assets = response.json()["assets"]
    assert len(assets) == 2
    assert {item["asset"]["issuer"] for item in assets} == {"G" + "B" * 55, "G" + "C" * 55}
    assert response.json()["metrics"]["trustline_count"] == 2
