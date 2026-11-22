from fastapi.testclient import TestClient

from app import stellar
from app.main import app


ADDRESS = "G" + "A" * 55


def test_account_operations_skip_malformed_horizon_records(monkeypatch):
    monkeypatch.setattr(stellar, "_get", lambda *args, **kwargs: {
        "_embedded": {"records": [None, "invalid", {"id": "valid", "type": "payment"}]}
    })

    response = TestClient(app).get(f"/accounts/{ADDRESS}/operations?limit=20")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["operations"]] == ["valid"]
