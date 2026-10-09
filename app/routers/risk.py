from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.stellar_address import is_valid_account_id
from app.stellar import score_account

router = APIRouter()


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(
        pattern=r"^(?:G[A-Z2-7]{55}|M[A-Z2-7]{68})$",
        description="Checksummed classic (G...) or muxed (M...) Stellar account ID",
    )

    @field_validator("address")
    @classmethod
    def validate_strkey_checksum(cls, value: str) -> str:
        if not is_valid_account_id(value):
            raise ValueError("address must be a valid classic or muxed Stellar account ID")
        return value


@router.post("/score")
def score_address(req: ScoreRequest):
    """Screen a Stellar account using its recent Horizon operations."""
    return score_account(req.address)
