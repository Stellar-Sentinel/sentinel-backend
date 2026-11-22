from app import stellar
from app.config import Settings


def test_malformed_v2_digest_is_reported_without_being_trusted(monkeypatch, caplog):
    settings = Settings(contract_id="C" + "A" * 55)

    def rpc(method, params, selected):
        if method == "getHealth":
            return {"latestLedger": 10, "oldestLedger": 1}
        return {
            "events": [{
                "id": "event-1",
                "topicJson": [
                    {"symbol": "flaggedv2"},
                    {"address": "agent"},
                    {"address": "subject"},
                    {"bytes": "invalid"},
                ],
                "valueJson": {"u32": 80},
            }]
        }

    monkeypatch.setattr(stellar, "_rpc", rpc)
    with caplog.at_level("WARNING", logger="sentinel.events"):
        events, _ = stellar.fetch_flag_event_page(10, settings=settings)

    assert events[0]["report_digest"] is None
    assert caplog.records[-1].event == "malformed_report_digest"
