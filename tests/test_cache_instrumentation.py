from app import stellar
from app.config import Settings


def test_cache_diagnostics_are_bounded_and_do_not_include_account(monkeypatch, caplog):
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
    settings = Settings(screening_cache_ttl_seconds=30)

    with caplog.at_level("DEBUG", logger="sentinel.cache"):
        stellar.score_account(address, settings=settings)
        stellar.score_account(address, settings=settings)

    assert [record.event for record in caplog.records] == ["miss", "hit"]
    assert all(record.ttl_seconds == 30 for record in caplog.records)
    assert address not in caplog.text
