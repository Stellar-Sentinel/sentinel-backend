from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import stellar
from app.main import app


def test_readiness_diagnostics_use_fixed_labels(monkeypatch, caplog):
    monkeypatch.setattr(stellar, "_get", lambda *args, **kwargs: {})

    def fail_rpc(*args, **kwargs):
        raise HTTPException(status_code=503, detail="private RPC payload")

    monkeypatch.setattr(stellar, "_rpc", fail_rpc)

    with caplog.at_level("INFO", logger="sentinel.readiness"):
        response = TestClient(app).get("/ready")

    assert response.status_code == 503
    record = caplog.records[-1]
    assert record.status == "not_ready"
    assert record.horizon == "ok"
    assert record.soroban_rpc == "unavailable"
    assert "private RPC payload" not in caplog.text
