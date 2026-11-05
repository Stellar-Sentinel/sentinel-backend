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


def report_digest(result: dict) -> bytes:
    """SHA-256 of the canonical off-chain report a `flaggedv2` event attests to.

    Canonical bytes are compact, sort-keyed JSON of the fields below. Any
    off-chain consumer that wants to verify a digest against a stored report
    must hash the same fields the same way.
    """
    canonical = json.dumps(
        {
            "address": result["address"],
            "score": result["score"],
            "risk_level": result["risk_level"],
            "threshold": result["threshold"],
            "scoring_policy_version": result["scoring_policy_version"],
            "as_of": result["as_of"],
        },
        sort_keys=True,
        separators=(",", ":"),
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
            raise RuntimeError(f"flag_anomaly_v2 simulation failed: {exc.simulate_transaction_response}") from exc

        tx.sign(self.keypair)
        sent = self.server.send_transaction(tx)
        if sent.status != SendTransactionStatus.PENDING:
            raise RuntimeError(f"flag_anomaly_v2 submission rejected: {sent.error_result_xdr}")

        deadline = time.monotonic() + _CONFIRMATION_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            outcome = self.server.get_transaction(sent.hash)
            if outcome.status != GetTransactionStatus.NOT_FOUND:
                if outcome.status != GetTransactionStatus.SUCCESS:
                    raise RuntimeError(f"flag_anomaly_v2 transaction failed: {outcome.result_xdr}")
                logger.info("Filed flag_anomaly_v2 (score=%s, tx=%s)", result["score"], sent.hash)
                return
            time.sleep(_CONFIRMATION_POLL_SECONDS)
        raise RuntimeError("flag_anomaly_v2 confirmation timed out")
