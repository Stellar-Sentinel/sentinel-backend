from app import stellar
from app.config import Settings


def test_bytes32_decoder_rejects_wrong_arity_and_non_hex_values():
    assert stellar._bytes32_hex({"bytes": "ab" * 32}) == "ab" * 32
    assert stellar._bytes32_hex({"bytes": "AB" * 32}) == "ab" * 32
    assert stellar._bytes32_hex({"bytes": "ab" * 31}) is None
    assert stellar._bytes32_hex({"bytes": "zz" * 32}) is None
    assert stellar._bytes32_hex({"bytes": None}) is None


def test_event_decoder_preserves_legacy_and_rejects_malformed_v2_digest(monkeypatch):
    settings = Settings(contract_id="C" + "A" * 55)

    def fake_rpc(method, params, selected):
        if method == "getHealth":
            return {"latestLedger": 100, "oldestLedger": 1}
        return {
            "events": [
                {
                    "id": "legacy",
                    "ledger": 90,
                    "topic": [
                        {"symbol": "flagged"},
                        {"address": {"accountId": "agent"}},
                        {"address": {"accountId": "subject"}},
                    ],
                    "value": {"u32": 70},
                },
                {
                    "id": "malformed-v2",
                    "ledger": 91,
                    "topicJson": [
                        {"symbol": "flaggedv2"},
                        {"address": "agent"},
                        {"address": "subject"},
                        {"bytes": "not-bytes32"},
                    ],
                    "valueJson": {"u32": 80},
                },
            ],
            "cursor": None,
        }

    monkeypatch.setattr(stellar, "_rpc", fake_rpc)
    events, cursor = stellar.fetch_flag_event_page(10, settings=settings)

    assert cursor is None
    assert events[0]["agent"] == "agent"
    assert events[0]["subject"] == "subject"
    assert events[0]["report_digest"] is None
    assert events[1]["score"] == 80
    assert events[1]["report_digest"] is None
