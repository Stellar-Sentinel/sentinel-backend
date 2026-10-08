from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from app.stellar import network_status
from app.routers import accounts, health, events, risk

settings = get_settings()

app = FastAPI(
    title="Stellar Sentinel API",
    description="Read-only Stellar account screening and Soroban contract event API.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(health.router)
app.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(risk.router, prefix="/risk", tags=["risk"])


@app.get("/network/status", tags=["network"])
def get_network_status():
    """Report Soroban RPC health and the RPC's current retained ledger window."""
    return network_status()

# TODO(#issue): no request logging middleware yet — every request should be
# logged as structured JSON (method, path, status, latency_ms).
# TODO(#issue): no auth/rate-limiting middleware yet — all routes are open.
