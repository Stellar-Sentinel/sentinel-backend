from app import stellar
from app.config import Settings


def test_screening_cache_does_not_cross_policy_versions(monkeypatch):
    address = "G" + "A" * 55
    calls = []

    def fake_get(url, params=None, settings=None):
        calls.append(url)
        if url.endswith("/accounts/" + address):
            return {"sequence": "10", "balances": []}
        return {"_embedded": {"records": []}}

    monkeypatch.setattr(stellar, "_get", fake_get)
    first = Settings(screening_cache_ttl_seconds=30, risk_policy_version="1.0.0")
    second = Settings(screening_cache_ttl_seconds=30, risk_policy_version="2.0.0")

    stellar.score_account(address, settings=first)
    stellar.score_account(address, settings=first)
    stellar.score_account(address, settings=second)

    assert len(calls) == 4
