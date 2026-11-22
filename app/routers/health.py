import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import stellar
from app.config import get_settings

router = APIRouter()
logger = logging.getLogger("sentinel.readiness")


@router.get("/health")
def health_check():
    return {"status": "ok"}


@router.get("/live", tags=["health"])
def liveness_check():
    """Confirm that the API process can serve requests without probing dependencies."""
    return {"status": "ok"}


@router.get("/ready", tags=["health"])
def readiness_check():
    """Report whether the configured Horizon and Soroban RPC services are reachable."""
    settings = get_settings()
    dependencies = {}

    try:
        stellar._get(f"{settings.horizon_url.rstrip('/')}/fee_stats", settings=settings)
        dependencies["horizon"] = "ok"
    except Exception:
        dependencies["horizon"] = "unavailable"

    try:
        stellar._rpc("getHealth", {}, settings=settings)
        dependencies["soroban_rpc"] = "ok"
    except Exception:
        dependencies["soroban_rpc"] = "unavailable"

    ready = all(status == "ok" for status in dependencies.values())
    logger.info(
        "readiness_probe status=%s horizon=%s soroban_rpc=%s",
        "ready" if ready else "not_ready",
        dependencies["horizon"],
        dependencies["soroban_rpc"],
        extra={
            "status": "ready" if ready else "not_ready",
            "horizon": dependencies["horizon"],
            "soroban_rpc": dependencies["soroban_rpc"],
        },
    )
    payload = {"status": "ready" if ready else "not_ready", "dependencies": dependencies}
    return payload if ready else JSONResponse(status_code=503, content=payload)
