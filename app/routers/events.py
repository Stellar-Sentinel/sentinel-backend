import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from app.models import EventsResponse, IngestionStatusResponse
from app.config import get_settings
from app.event_store import EventStoreSchemaError, get_event_store
from app.metrics import ingestion_metrics
from app.stellar import _public_endpoint, cursor_ledger, ingestion_scope, sync_flag_events

router = APIRouter()
logger = logging.getLogger("sentinel.events")


@router.get("/", response_model=EventsResponse)
def list_events(limit: int = Query(20, ge=1, le=100), cursor: str | None = Query(None, min_length=1, max_length=512)):
    """Read indexed `flagged` events and advance ingestion from Soroban RPC."""
    settings = get_settings()
    if not settings.contract_id:
        raise HTTPException(status_code=503, detail="CONTRACT_ID is required to read Stellar Sentinel on-chain events")
    store = get_event_store(settings.event_store_path)
    ingestion_status = "current"
    try:
        sync_flag_events(store, settings)
    except HTTPException as exc:
        if exc.status_code not in {502, 503}:
            raise
        ingestion_status = "stale"
    result = store.page(limit, ingestion_scope(settings), cursor)
    result["source"] = {
        "rpc_url": _public_endpoint(settings.soroban_rpc_url),
        "network": settings.network_passphrase,
        "contract_id": settings.contract_id,
        "ingestion_status": ingestion_status,
    }
    return result


@router.get("/ingestion", response_model=IngestionStatusResponse)
def get_ingestion_status():
    """Report this process's event-ingestion health without contacting Soroban RPC.

    Counters are per process and reset on restart; the cursor comes from the
    shared SQLite store. Only the cursor's ledger is exposed, never the cursor.
    """
    settings = get_settings()
    snapshot = ingestion_metrics.snapshot()
    payload = {
        "enabled": bool(settings.contract_id),
        "store": None,
        "cursor": None,
        **snapshot,
        "limits": {
            "page_size": settings.event_ingest_page_size,
            "max_pages": settings.event_ingest_max_pages,
            "sync_budget_seconds": settings.event_ingest_sync_budget_seconds,
            "interval_seconds": settings.event_ingest_interval_seconds,
            "max_backoff_seconds": settings.event_ingest_max_backoff_seconds,
            "cursor_recovery": settings.event_ingest_cursor_recovery,
        },
    }
    if not payload["enabled"]:
        payload["state"] = "disabled"
        return payload

    try:
        stored_cursor = get_event_store(settings.event_store_path).ingestion_cursor(ingestion_scope(settings))
    except (sqlite3.Error, EventStoreSchemaError):
        logger.warning(
            "event_ingestion_status store=unavailable",
            extra={"event": "ingestion_status", "store": "unavailable"},
        )
        payload["store"] = "unavailable"
    else:
        payload["store"] = "ok"
        payload["cursor"] = {"present": stored_cursor is not None, "ledger": cursor_ledger(stored_cursor)}

    last_sync = snapshot["last_sync"]
    if last_sync is None:
        payload["state"] = "unknown"
    else:
        payload["state"] = "failing" if last_sync["result"] == "failed" else "ok"
    return payload
