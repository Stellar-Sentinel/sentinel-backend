from fastapi import APIRouter, Path, Query

from app.stellar import list_account_operations

router = APIRouter()


@router.get("/{address}/operations")
def account_operations(
    address: str = Path(pattern=r"^G[A-Z2-7]{55}$", description="Classic Stellar account public key"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=256),
):
    """Return normalized, cursor-paginated Horizon operations for an account."""
    return list_account_operations(address, limit=limit, cursor=cursor)
