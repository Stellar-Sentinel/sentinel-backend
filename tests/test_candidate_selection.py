from app.agents import monitor
from app.config import Settings


def test_candidate_selection_is_bounded_ordered_and_excludes_invalid_records(monkeypatch):
    captured = {}
    source_a = "G" + "A" * 55
    source_b = "G" + "B" * 55

    def fake_get(url, *, params, settings):
        captured.update(params)
        return {
            "_embedded": {
                "records": [
                    {"source_account": source_a},
                    {"source_account": source_b},
                    {"source_account": source_a},
                    {"source_account": ""},
                    {"source_account": None},
                    {"source_account": 42},
                    "malformed",
                ]
            }
        }

    monkeypatch.setattr(monitor, "_get", fake_get)
    accounts = monitor.recent_network_accounts(Settings(), limit=3)

    assert accounts == [source_a, source_b]
    assert captured == {"order": "desc", "limit": 3, "include_failed": "false"}


def test_candidate_selection_does_not_make_extra_upstream_requests(monkeypatch):
    calls = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return {"_embedded": {"records": []}}

    monkeypatch.setattr(monitor, "_get", fake_get)
    monitor.recent_network_accounts(Settings(), limit=20)

    assert len(calls) == 1
