from app import stellar
from app.config import Settings


def test_event_decoder_accepts_legacy_names_and_nested_addresses(monkeypatch):
    settings = Settings(contract_id="C" + "A" * 55)

    def rpc(method, params, selected):
        if method == "getHealth":
            return {"latestLedger": 20, "oldestLedger": 1}
        return {
            "events": [{
                "id": "legacy-event",
                "ledger": 19,
                "topic": [
                    {"symbol": "flagged"},
                    {"address": {"accountId": "agent"}},
                    {"address": {"contractId": "subject"}},
                ],
                "value": {"u32": 70},
            }]
        }

    monkeypatch.setattr(stellar, "_rpc", rpc)
    events, _ = stellar.fetch_flag_event_page(10, settings=settings)

    assert events == [{
        "id": "legacy-event",
        "ledger": 19,
        "created_at": None,
        "agent": "agent",
        "subject": "subject",
        "score": 70,
        "report_digest": None,
        "contract_id": settings.contract_id,
        "tx_hash": None,
    }]
