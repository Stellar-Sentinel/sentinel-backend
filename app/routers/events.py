from fastapi import APIRouter, HTTPException, Query

from app.models import EventsResponse
from app.config import get_settings
from app.event_store import get_event_store
from app.stellar import sync_flag_events

router = APIRouter()


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
    scope = f"{settings.network_passphrase}:{settings.contract_id}"
    result = store.page(limit, scope, cursor)
    result["source"] = {
        "rpc_url": settings.soroban_rpc_url,
        "network": settings.network_passphrase,
        "contract_id": settings.contract_id,
        "ingestion_status": ingestion_status,
    }
    return result
