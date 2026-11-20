from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import stellar
from app.main import app


def test_readiness_reports_mixed_dependency_state_without_exception_details(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(stellar, "_get", lambda *args, **kwargs: {"fee_stats": True})

    def unavailable_rpc(*args, **kwargs):
        raise HTTPException(status_code=503, detail="private RPC response")

    monkeypatch.setattr(stellar, "_rpc", unavailable_rpc)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"horizon": "ok", "soroban_rpc": "unavailable"},
    }
    assert "private RPC response" not in response.text
