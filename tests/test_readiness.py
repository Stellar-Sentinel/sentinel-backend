from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import stellar
from app.main import app


client = TestClient(app)


def test_readiness_probes_both_dependencies_on_every_request(monkeypatch):
    calls = []

    def horizon(*args, **kwargs):
        calls.append(("horizon", kwargs["settings"].request_timeout_seconds))
        return {}

    def rpc(*args, **kwargs):
        calls.append(("soroban_rpc", kwargs["settings"].request_timeout_seconds))
        return {"status": "healthy"}

    monkeypatch.setattr(stellar, "_get", horizon)
    monkeypatch.setattr(stellar, "_rpc", rpc)

    assert client.get("/ready").status_code == 200
    assert client.get("/ready").status_code == 200
    assert calls == [
        ("horizon", 8.0),
        ("soroban_rpc", 8.0),
        ("horizon", 8.0),
        ("soroban_rpc", 8.0),
    ]


def test_readiness_reports_degraded_dependencies_without_upstream_details(monkeypatch):
    def fail(*args, **kwargs):
        raise HTTPException(status_code=504, detail="private upstream URL")

    monkeypatch.setattr(stellar, "_get", fail)
    monkeypatch.setattr(stellar, "_rpc", fail)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"horizon": "unavailable", "soroban_rpc": "unavailable"},
    }
    assert "private upstream URL" not in response.text
