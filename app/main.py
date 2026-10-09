import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.middleware.rate_limit import ScreeningRateLimitMiddleware
from app.stellar import close_http_client, network_status, set_http_client, sync_flag_events
from app.routers import accounts, health, events, risk
from app.models import NetworkStatusResponse
from app.request_id import RequestIDMiddleware
from app.request_logging import RequestLoggingMiddleware
from app.event_store import get_event_store

settings = get_settings()
logger = logging.getLogger(__name__)


async def _ingest_events_forever():
    if not settings.contract_id:
        return
    store = get_event_store(settings.event_store_path)
    while True:
        try:
            await asyncio.to_thread(sync_flag_events, store, settings)
        except Exception as exc:
            logger.warning("Soroban event ingestion failed (%s)", type(exc).__name__)
        await asyncio.sleep(settings.event_ingest_interval_seconds)


@asynccontextmanager
async def lifespan(_app):
    client = httpx.Client(
        timeout=settings.request_timeout_seconds,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    set_http_client(client)
    task = asyncio.create_task(_ingest_events_forever())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        close_http_client()


app = FastAPI(
    title="Stellar Sentinel API",
    description="Read-only Stellar account screening and Soroban contract event API.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    ScreeningRateLimitMiddleware,
    requests=settings.screening_rate_limit_requests,
    window_seconds=settings.screening_rate_limit_window_seconds,
    trusted_proxies=settings.trusted_proxy_networks,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(health.router)
app.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(risk.router, prefix="/risk", tags=["risk"])


@app.get("/network/status", tags=["network"], response_model=NetworkStatusResponse)
def get_network_status():
    """Report Soroban RPC health and the RPC's current retained ledger window."""
    return network_status()

# TODO(#issue): no request logging middleware yet — every request should be
# logged as structured JSON (method, path, status, latency_ms).
