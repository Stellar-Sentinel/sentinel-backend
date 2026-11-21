import json

from fastapi.testclient import TestClient

from app.main import app


def test_request_metrics_contract_remains_privacy_safe(caplog):
    with caplog.at_level("INFO", logger="sentinel.request"):
        response = TestClient(app).get("/missing/GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA?api_key=secret")

    record = json.loads(caplog.records[-1].message)
    assert response.status_code == 404
    assert record["event"] == "http.request"
    assert record["schema_version"] == 1
    assert record["status"] == 404
    assert "secret" not in caplog.text
    assert "GAAAA" not in caplog.text
