import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.middleware.api_key import ApiKeyMiddleware
from app.middleware.rate_limit import ScreeningRateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.stellar import (
    classify_ingestion_failure,
    close_http_client,
    ingestion_retry_delay,
    network_status,
    set_http_client,
    sync_flag_events,
)
from app.routers import accounts, health, events, risk
from app.models import NetworkStatusResponse
from app.request_id import RequestIDMiddleware
from app.request_logging import RequestLoggingMiddleware
from app.event_store import get_event_store
from app.agents.monitor import MonitoringAgent

settings = get_settings()
logger = logging.getLogger(__name__)


async def _ingest_events_forever():
    if not settings.contract_id:
        return
    store = get_event_store(settings.event_store_path)
    consecutive_failures = 0
    while True:
        try:
            await asyncio.to_thread(sync_flag_events, store, settings)
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            # sync_flag_events already logged the failure detail; this line
            # records the backoff decision with the same fixed label.
            failure = classify_ingestion_failure(exc)
            delay = ingestion_retry_delay(consecutive_failures, settings)
            logger.warning(
                "event_ingestion_backoff failure=%s consecutive_failures=%s next_attempt_seconds=%s",
                failure, consecutive_failures, delay,
                extra={"event": "ingestion_backoff", "failure": failure,
                       "consecutive_failures": consecutive_failures, "next_attempt_seconds": delay},
            )
        await asyncio.sleep(ingestion_retry_delay(consecutive_failures, settings))


async def _run_agent_forever():
    if not (settings.agent_enabled and settings.agent_secret_key and settings.contract_id):
        return
    try:
        agent = MonitoringAgent(settings)
    except ValueError as exc:
        logger.warning("Monitoring agent disabled: %s", exc)
        return
    logger.info(
        "Monitoring agent authorization configured (contract_id=%s network=%s)",
        settings.contract_id,
        settings.network_passphrase,
    )
    while True:
        try:
            filed = await asyncio.to_thread(agent.run_once)
            if filed:
                logger.info("Monitoring agent filed %s flag(s) this cycle", filed)
        except Exception as exc:
            logger.warning("Monitoring agent cycle failed (%s)", type(exc).__name__)
        await asyncio.sleep(settings.agent_poll_interval_seconds)


@asynccontextmanager
async def lifespan(_app):
    if settings.contract_id:
        # Open (and migrate) the event store before serving. A schema that
        # cannot be migrated must stop startup, not surface later as failing
        # requests or an ingestion task that died in the background.
        await asyncio.to_thread(get_event_store, settings.event_store_path)
    client = httpx.Client(
        timeout=settings.request_timeout_seconds,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    set_http_client(client)
    tasks = [asyncio.create_task(_ingest_events_forever()), asyncio.create_task(_run_agent_forever())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        close_http_client()


app = FastAPI(
    title="Stellar Sentinel API",
    description=(
        "Read-only Stellar account screening and Soroban contract event API. "
        "Browser access is limited to exact origins in CORS_ORIGINS; forwarded "
        "client addresses affect screening rate limits only when the direct peer "
        "belongs to TRUSTED_PROXY_CIDRS. See docs/CORS_AND_PROXY_OPERATIONS.md."
    ),
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
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)
if settings.api_key:
    app.add_middleware(ApiKeyMiddleware, api_key=settings.api_key)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(health.router)
app.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(risk.router, prefix="/risk", tags=["risk"])


@app.get("/network/status", tags=["network"], response_model=NetworkStatusResponse)
def get_network_status():
    """Report Soroban RPC health and the RPC's current retained ledger window."""
    return network_status()
