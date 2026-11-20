"""Autonomous monitoring agent.

Polls recent Stellar network activity, screens candidate accounts with the
same scoring used by `POST /risk/score`, and signs/submits `flag_anomaly_v2`
for any account that crosses the configured threshold. Disabled unless
`AGENT_ENABLED=true` and `AGENT_SECRET_KEY` are both set; the configured key
must already be authorized as a responder on `CONTRACT_ID` (admin-only
`authorize_responder` call) or every submission will fail authorization.
"""
import hashlib
import json
import logging
import time

from stellar_sdk import Keypair, SorobanServer, TransactionBuilder, scval
from stellar_sdk.exceptions import PrepareTransactionException
from stellar_sdk.soroban_rpc import GetTransactionStatus, SendTransactionStatus

from app.config import Settings
from app.stellar import _get, score_account

logger = logging.getLogger("sentinel.agent")

_CONFIRMATION_TIMEOUT_SECONDS = 30
_CONFIRMATION_POLL_SECONDS = 3
REPORT_DIGEST_SCHEMA_VERSION = 1


def canonical_report(result: dict) -> dict:
    """Return the versioned, normalized fields authenticated by a report digest."""
    return {
        "as_of": None if result.get("as_of") is None else str(result["as_of"]),
        "address": str(result["address"]),
        "report_schema_version": REPORT_DIGEST_SCHEMA_VERSION,
        "risk_level": str(result["risk_level"]),
        "score": int(result["score"]),
        "scoring_policy_version": str(result["scoring_policy_version"]),
        "threshold": int(result["threshold"]),
    }


def _account_label(address: str) -> str:
    """Keep public-account diagnostics bounded without logging full addresses."""
    return address[-8:] if isinstance(address, str) else "unknown"


def _log_transaction_event(event: str, address: str, *, score: int | None = None, tx_present: bool = False) -> None:
    logger.info(
        "agent_transaction event=%s account_suffix=%s score=%s tx_present=%s",
        event,
        _account_label(address),
        score,
        tx_present,
        extra={
            "event": event,
            "account_suffix": _account_label(address),
            "score": score,
            "tx_present": tx_present,
        },
    )


def report_digest(result: dict) -> bytes:
    """SHA-256 of the versioned report `flaggedv2` events attest to.

    Canonical bytes are compact, sort-keyed JSON with explicit number/string/
    null normalization. Consumers can select the canonical schema by its
    ``report_schema_version`` before independently checking the digest.
    """
    canonical = json.dumps(
        canonical_report(result),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).digest()


def recent_network_accounts(settings: Settings, limit: int) -> list[str]:
    """Return distinct source accounts from the most recent network-wide operations."""
    url = f"{settings.horizon_url.rstrip('/')}/operations"
    payload = _get(url, params={"order": "desc", "limit": limit, "include_failed": "false"}, settings=settings)
    records = payload.get("_embedded", {}).get("records", [])
    seen: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        account = record.get("source_account")
        if isinstance(account, str) and account not in seen:
            seen.append(account)
    return seen


class MonitoringAgent:
    """Holds the agent's signing key and files flags for over-threshold accounts."""

    def __init__(self, settings: Settings):
        if not settings.agent_secret_key:
            raise ValueError("AGENT_SECRET_KEY is required to run the monitoring agent")
        if not settings.contract_id:
            raise ValueError("CONTRACT_ID is required to run the monitoring agent")
        self.settings = settings
        self.keypair = Keypair.from_secret(settings.agent_secret_key)
        self.server = SorobanServer(settings.soroban_rpc_url)
        self._last_flagged_at: dict[str, float] = {}

    def _on_cooldown(self, address: str) -> bool:
        last = self._last_flagged_at.get(address)
        return last is not None and (time.monotonic() - last) < self.settings.agent_flag_cooldown_seconds

    def run_once(self) -> int:
        """Score one batch of recently active accounts and file any due flags.

        Returns the number of flags successfully filed in this cycle.
        """
        candidates = recent_network_accounts(self.settings, self.settings.agent_scan_candidate_limit)
        logger.info(
            "Selected monitoring candidates (count=%d limit=%d)",
            len(candidates),
            self.settings.agent_scan_candidate_limit,
        )
        filed = 0
        for address in candidates:
            if address == self.keypair.public_key or self._on_cooldown(address):
                continue
            try:
                result = score_account(address, self.settings)
            except Exception as exc:
                logger.warning("Agent scoring failed for a candidate (%s)", type(exc).__name__)
                continue
            if not result.get("threshold_exceeded"):
                continue
            self._last_flagged_at[address] = time.monotonic()
            try:
                self._file_flag(address, result)
                filed += 1
            except Exception as exc:
                logger.warning("Agent flag submission failed (%s)", type(exc).__name__)
        return filed

    def _file_flag(self, address: str, result: dict) -> None:
        digest = report_digest(result)
        source = self.server.load_account(self.keypair.public_key)
        tx = (
            TransactionBuilder(source, self.settings.network_passphrase, base_fee=10_000)
            .set_timeout(30)
            .append_invoke_contract_function_op(
                contract_id=self.settings.contract_id,
                function_name="flag_anomaly_v2",
                parameters=[
                    scval.to_address(self.keypair.public_key),
                    scval.to_address(address),
                    scval.to_uint32(int(result["score"])),
                    scval.to_bytes(digest),
                ],
            )
            .build()
        )
        try:
            tx = self.server.prepare_transaction(tx)
        except PrepareTransactionException as exc:
            _log_transaction_event("simulation_failed", address, score=int(result["score"]))
            raise RuntimeError("flag_anomaly_v2 simulation failed") from exc

        tx.sign(self.keypair)
        sent = self.server.send_transaction(tx)
        if sent.status != SendTransactionStatus.PENDING:
            _log_transaction_event("submission_rejected", address, score=int(result["score"]))
            raise RuntimeError("flag_anomaly_v2 submission rejected")

        deadline = time.monotonic() + _CONFIRMATION_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            outcome = self.server.get_transaction(sent.hash)
            if outcome.status != GetTransactionStatus.NOT_FOUND:
                if outcome.status != GetTransactionStatus.SUCCESS:
                    _log_transaction_event("confirmation_failed", address, score=int(result["score"]), tx_present=True)
                    raise RuntimeError("flag_anomaly_v2 transaction failed")
                _log_transaction_event("confirmed", address, score=int(result["score"]), tx_present=True)
                return
            time.sleep(_CONFIRMATION_POLL_SECONDS)
        _log_transaction_event("confirmation_timed_out", address, score=int(result["score"]), tx_present=True)
        raise RuntimeError("flag_anomaly_v2 confirmation timed out")
