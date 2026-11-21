from app.event_store import EventStore


SCOPE = "testnet:contract"


def event(event_id: str, ledger: int, score: int) -> dict:
    return {
        "id": event_id,
        "ledger": ledger,
        "created_at": f"ledger-{ledger}",
        "agent": "agent",
        "subject": "subject",
        "score": score,
        "contract_id": "contract",
        "tx_hash": f"tx-{event_id}",
    }


def test_duplicate_replay_is_idempotent_and_cursor_advances(tmp_path):
    path = tmp_path / "events.sqlite3"
    store = EventStore(str(path))

    store.save_page([event("same-event", 10, 70)], "cursor-1", SCOPE)
    store.save_page([event("same-event", 10, 99)], "cursor-2", SCOPE)

    reopened = EventStore(str(path))
    page = reopened.page(limit=10, scope=SCOPE)

    assert page["events"] == [
        {
            **event("same-event", 10, 70),
            "report_digest": None,
        }
    ]
    assert reopened.ingestion_cursor(SCOPE) == "cursor-2"


def test_replay_for_another_scope_is_not_deduplicated(tmp_path):
    store = EventStore(str(tmp_path / "events.sqlite3"))
    store.save_page([event("same-event", 10, 70)], "cursor-a", "network-a:contract")
    store.save_page([event("same-event", 10, 90)], "cursor-b", "network-b:contract")

    assert store.page(limit=10, scope="network-a:contract")["events"][0]["score"] == 70
    assert store.page(limit=10, scope="network-b:contract")["events"][0]["score"] == 90
