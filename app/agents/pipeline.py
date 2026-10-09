"""Deterministic baseline risk scoring for caller-supplied activity metrics.

This is a transparent prototype heuristic, not a trained model or a substitute
for chain-derived activity. Keep the caps and weights visible until calibrated
against labeled Stellar data.
"""

import math

TX_COUNT_CAP = 100
VOLUME_XLM_CAP = 1_000_000
RISK_THRESHOLD = 70


def run_pipeline(address: str, recent_tx_count: int, recent_tx_volume: float) -> dict:
    """Score activity over the caller-declared recent window.

    Transaction count contributes 60 points and volume (in XLM) contributes
    40 points. Each component saturates at its documented cap.
    """
    if (isinstance(recent_tx_count, bool) or not isinstance(recent_tx_count, int)
            or recent_tx_count < 0):
        raise ValueError("recent_tx_count must be a non-negative integer")
    if isinstance(recent_tx_volume, bool) or not isinstance(recent_tx_volume, (int, float)):
        raise ValueError("recent_tx_volume must be a finite non-negative number")
    try:
        volume = float(recent_tx_volume)
    except OverflowError as exc:
        raise ValueError("recent_tx_volume must be a finite non-negative number") from exc
    if not math.isfinite(volume) or volume < 0:
        raise ValueError("recent_tx_volume must be a finite non-negative number")

    tx_score = min(recent_tx_count / TX_COUNT_CAP, 1) * 60
    volume_score = min(volume / VOLUME_XLM_CAP, 1) * 40
    score = round(tx_score + volume_score)

    if score >= RISK_THRESHOLD:
        risk_level = "high"
    elif score >= 40:
        risk_level = "elevated"
    else:
        risk_level = "low"

    return {
        "address": address,
        "score": score,
        "risk_level": risk_level,
        "threshold": RISK_THRESHOLD,
        "threshold_exceeded": score >= RISK_THRESHOLD,
        "score_breakdown": {
            "transaction_activity": round(tx_score, 2),
            "transaction_volume": round(volume_score, 2),
        },
        "window": "caller-supplied recent activity; duration is not currently enforced",
        "on_chain_action": "none",
    }
