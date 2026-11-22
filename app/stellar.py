"""Read-only clients for Horizon and Stellar RPC."""
from datetime import datetime, timedelta, timezone
import base64
import copy
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit
from email.utils import parsedate_to_datetime
import time
import math
import sqlite3
import threading
import logging

import httpx
from fastapi import HTTPException

from app.config import Settings, get_settings
from app.event_store import CursorConflictError
from app.metrics import ingestion_metrics

_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
HORIZON_OPERATION_PAGE_SIZE = 200
logger = logging.getLogger(__name__)
event_logger = logging.getLogger("sentinel.events")
_screening_cache = OrderedDict()
_screening_cache_lock = threading.Lock()


def clear_screening_cache() -> None:
    """Clear the bounded in-process screening cache."""
    with _screening_cache_lock:
        _screening_cache.clear()


def _cached_screening(key: tuple, ttl_seconds: int):
    if ttl_seconds <= 0:
        return None
    now = time.monotonic()
    with _screening_cache_lock:
        entry = _screening_cache.get(key)
        if entry is None:
            return None
        expires_at, result = entry
        if expires_at <= now:
            del _screening_cache[key]
            return None
        _screening_cache.move_to_end(key)
        return copy.deepcopy(result)


def _store_screening(key: tuple, result: dict, settings: Settings) -> None:
    if settings.screening_cache_ttl_seconds <= 0:
        return
    with _screening_cache_lock:
        _screening_cache[key] = (
            time.monotonic() + settings.screening_cache_ttl_seconds,
            copy.deepcopy(result),
        )
        _screening_cache.move_to_end(key)
        while len(_screening_cache) > settings.screening_cache_max_entries:
            _screening_cache.popitem(last=False)


def _retry_after_seconds(response: httpx.Response, settings: Settings, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    delay = None
    if retry_after:
        try:
            delay = max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                delay = None
    if delay is None:
        delay = settings.upstream_retry_backoff_seconds * (2 ** attempt)
    return min(delay, settings.upstream_retry_after_cap_seconds)


def _retry_upstream(url: str) -> str:
    """Return only the upstream host for safe, bounded retry diagnostics."""
    try:
        return urlsplit(url).netloc or "unknown"
    except (TypeError, ValueError):
        return "unknown"


def _log_retry(operation: str, url: str, attempt: int, retries: int, delay: float, reason: str) -> None:
    logger.warning(
        "upstream_retry_scheduled operation=%s upstream=%s attempt=%d max_retries=%d delay_seconds=%.3f reason=%s",
        operation,
        _retry_upstream(url),
        attempt + 1,
        retries,
        delay,
        reason,
        extra={
            "operation": operation,
            "upstream": _retry_upstream(url),
            "attempt": attempt + 1,
            "max_retries": retries,
            "delay_seconds": delay,
            "reason": reason,
        },
    )


def _request_with_retries(
    method,
    url: str,
    settings: Settings,
    *,
    operation: str = "upstream_request",
    **kwargs,
) -> httpx.Response:
    retries = settings.upstream_max_retries
    for attempt in range(retries + 1):
        try:
            response = method(url, **kwargs)
        except httpx.TransportError:
            if attempt >= retries:
                logger.warning(
                    "upstream_retry_exhausted operation=%s upstream=%s attempts=%d reason=transport",
                    operation,
                    _retry_upstream(url),
                    attempt + 1,
                    extra={
                        "operation": operation,
                        "upstream": _retry_upstream(url),
                        "attempts": attempt + 1,
                        "reason": "transport",
                    },
                )
                raise
            delay = settings.upstream_retry_backoff_seconds * (2 ** attempt)
            delay = min(delay, settings.upstream_retry_after_cap_seconds)
            _log_retry(operation, url, attempt, retries, delay, "transport")
            time.sleep(delay)
            continue

        if response.status_code in _RETRYABLE_STATUS_CODES and attempt < retries:
            delay = _retry_after_seconds(response, settings, attempt)
            _log_retry(operation, url, attempt, retries, delay, f"http_{response.status_code}")
            time.sleep(delay)
            continue
        if response.status_code in _RETRYABLE_STATUS_CODES:
            logger.warning(
                "upstream_retry_exhausted operation=%s upstream=%s attempts=%d reason=http_%d",
                operation,
                _retry_upstream(url),
                attempt + 1,
                response.status_code,
                extra={
                    "operation": operation,
                    "upstream": _retry_upstream(url),
                    "attempts": attempt + 1,
                    "reason": f"http_{response.status_code}",
                },
            )
        return response

    raise RuntimeError("upstream retry loop ended unexpectedly")
_http_client: httpx.Client | None = None


def set_http_client(client: httpx.Client) -> None:
    """Install the application-scoped connection pool used by upstream calls."""
    global _http_client
    if _http_client is not None and _http_client is not client:
        _http_client.close()
    _http_client = client


def close_http_client() -> None:
    """Close and clear the application-scoped connection pool."""
    global _http_client
    client, _http_client = _http_client, None
    if client is not None:
        client.close()


def _get_response(url: str, params: dict | None, settings: Settings):
    if _http_client is not None:
        return _http_client.get(url, params=params, timeout=settings.request_timeout_seconds)
    return httpx.get(url, params=params, timeout=settings.request_timeout_seconds)


def _post_response(url: str, payload: dict, settings: Settings):
    if _http_client is not None:
        return _http_client.post(url, json=payload, timeout=settings.request_timeout_seconds)
    return httpx.post(url, json=payload, timeout=settings.request_timeout_seconds)


def _finite_float(value) -> float:
    """Parse an upstream numeric field without allowing NaN or infinity through."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _public_endpoint(url: str) -> str:
    """Remove credentials and opaque query data before exposing an endpoint URL."""
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return "[redacted]"
    if hostname is None:
        return f"{parts.scheme}://[redacted]" if parts.scheme else "[redacted]"
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parts.port
    except ValueError:
        return f"{parts.scheme}://[redacted]"
    netloc = f"{hostname}:{port}" if port is not None else hostname
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _get(url: str, params: dict | None = None, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    try:
        response = _request_with_retries(
            lambda target, **kwargs: _get_response(target, kwargs.get("params"), settings),
            url,
            settings,
            operation="horizon_get",
            params=params,
            timeout=settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Stellar account was not found on the configured network") from exc
        raise HTTPException(status_code=502, detail="Horizon request failed") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Unable to reach the configured Stellar data service") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Horizon returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Horizon returned an invalid response")
    return payload


def _asset_context(account: dict) -> tuple[list[dict], int, float]:
    """Return displayable balances and count only issued-asset trustlines.

    Horizon also exposes liquidity-pool shares in ``balances``. They are
    account assets, but they are not trustlines, so they must not inflate the
    trustline metric used by operators.
    """
    assets = []
    trustline_count = 0
    native_balance = 0.0
    balances = account.get("balances", [])
    if not isinstance(balances, list):
        return assets, trustline_count, native_balance

    for balance in balances:
        if not isinstance(balance, dict):
            continue
        asset_type = balance.get("asset_type")
        amount = balance.get("balance")
        if not isinstance(asset_type, str) or amount is None:
            continue
        if asset_type not in {"native", "credit_alphanum4", "credit_alphanum12", "liquidity_pool_shares"}:
            continue
        try:
            numeric_amount = float(amount)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric_amount):
            continue

        if asset_type == "native":
            native_balance = numeric_amount
            asset = {"type": "native", "code": "XLM"}
        else:
            asset = {"type": asset_type}
            if balance.get("asset_code") is not None:
                asset["code"] = balance["asset_code"]
            if balance.get("asset_issuer") is not None:
                asset["issuer"] = balance["asset_issuer"]
            if asset_type.startswith("credit_") and not (
                asset.get("code") and asset.get("issuer")
            ):
                continue
            if balance.get("liquidity_pool_id") is not None:
                asset["liquidity_pool_id"] = balance["liquidity_pool_id"]
            if asset_type.startswith("credit_"):
                trustline_count += 1
        assets.append({"asset": asset, "balance": str(amount)})

    return assets, trustline_count, native_balance


class SorobanRpcError(HTTPException):
    """A JSON-RPC application error, keeping only the numeric error code.

    The upstream message is deliberately dropped: it is provider-controlled
    text and must not reach clients or logs.
    """

    def __init__(self, code: int | None):
        super().__init__(status_code=502, detail="Soroban RPC returned an error")
        self.code = code


class IngestionCursorRejectedError(SorobanRpcError):
    """RPC refused the persisted ingestion cursor and recovery did not apply."""


class UpstreamDataError(HTTPException):
    """Soroban RPC answered, but with data the indexer refuses to use.

    ``failure`` is one of a fixed set of telemetry labels; ``detail`` stays
    generic and never echoes upstream content.
    """

    def __init__(self, failure: str, detail: str):
        super().__init__(status_code=502, detail=detail)
        self.failure = failure


def _rpc(method: str, params: dict, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    try:
        response = _request_with_retries(
            lambda target, **kwargs: _post_response(target, kwargs.get("json"), settings),
            settings.soroban_rpc_url,
            settings,
            operation="soroban_rpc_post",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="Soroban RPC request failed") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Unable to reach configured Soroban RPC") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Soroban RPC returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Soroban RPC returned an invalid response")
    if payload.get("error"):
        error = payload["error"]
        code = error.get("code") if isinstance(error, dict) else None
        raise SorobanRpcError(code if isinstance(code, int) and not isinstance(code, bool) else None)
    result = payload.get("result", {})
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Soroban RPC returned an invalid result")
    return result


def _scan_account_operations(address: str, settings: Settings) -> tuple[list[dict], int, bool]:
    """Read the bounded recent-operation sample across Horizon pages."""
    scan_limit = settings.operation_scan_limit
    if scan_limit <= 0:
        return [], scan_limit, False

    url = f"{settings.horizon_url.rstrip('/')}/accounts/{address}/operations"
    records: list[dict] = []
    cursor: str | None = None
    may_be_incomplete = False
    while len(records) < scan_limit:
        page_limit = min(HORIZON_OPERATION_PAGE_SIZE, scan_limit - len(records))
        params = {"limit": page_limit, "order": "desc", "include_failed": "false"}
        if cursor is not None:
            params["cursor"] = cursor
        payload = _get(url, params=params, settings=settings)
        embedded = payload.get("_embedded")
        page = embedded.get("records", []) if isinstance(embedded, dict) else []
        if not isinstance(page, list) or not page:
            break
        page_records = [record for record in page[:page_limit] if isinstance(record, dict)]
        if not page_records:
            break
        records.extend(page_records)

        if len(records) >= scan_limit:
            may_be_incomplete = len(page) >= page_limit
            break
        if len(page) < page_limit:
            break

        next_cursor = page_records[-1].get("id")
        if next_cursor is None or str(next_cursor) == cursor:
            may_be_incomplete = True
            break
        cursor = str(next_cursor)

    return records, scan_limit, may_be_incomplete


def score_account(address: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    key = (
        settings.network_passphrase,
        settings.horizon_url.rstrip("/"),
        address,
        settings.model_dump_json(),
    )
    cached = _cached_screening(key, settings.screening_cache_ttl_seconds)
    if cached is not None:
        return cached
    result = _score_account_uncached(address, settings)
    _store_screening(key, result, settings)
    return copy.deepcopy(result)


def _score_account_uncached(address: str, settings: Settings) -> dict:
    account = _get(f"{settings.horizon_url.rstrip('/')}/accounts/{address}", settings=settings)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=settings.activity_window_days)
    records, scan_limit, may_be_incomplete = _scan_account_operations(address, settings)
    recent = []
    for op in records:
        if not isinstance(op, dict) or not isinstance(op.get("created_at"), str):
            continue
        try:
            created = datetime.fromisoformat(op["created_at"].replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            continue
        if created.tzinfo is None:
            continue
        if created >= window_start:
            recent.append(op)

    counterparties: set[str] = set()
    volume = 0.0
    transfers = 0
    for op in recent:
        if op.get("type") not in {"payment", "create_account", "path_payment_strict_receive", "path_payment_strict_send"}:
            continue
        # create_account operations carry the new account as `account` (and the
        # funder as `funder`/`source_account`), not `to`/`from`, which are null here.
        if op.get("type") == "create_account":
            counterparty_fields = (op.get("source_account"), op.get("account"), op.get("funder"))
        else:
            counterparty_fields = (op.get("source_account"), op.get("to"), op.get("from"))
        for account_id in counterparty_fields:
            if account_id and account_id != address:
                counterparties.add(account_id)
        # Only native XLM amounts are included; asset amounts are never mixed into XLM totals.
        if op.get("type") == "create_account":
            volume += abs(_finite_float(op.get("starting_balance", "0")))
        elif op.get("asset_type") in (None, "native"):
            volume += abs(_finite_float(op.get("amount", "0")))
        transfers += 1

    signals = []
    score = 0
    def add_signal(signal_id: str, label: str, value, severity: str, points: int, explanation: str):
        nonlocal score
        score += points
        signals.append({"id": signal_id, "label": label, "value": value, "severity": severity,
                        "points": points, "explanation": explanation,
                        "source": "Stellar Horizon account operations", "window": f"last {settings.activity_window_days} days"})

    if len(recent) >= settings.risk_activity_burst_min_operations:
        operation_count = settings.risk_activity_burst_min_operations
        operation_noun = "operation" if operation_count == 1 else "operations"
        operation_verb = "was" if operation_count == 1 else "were"
        add_signal(
            "activity_burst", "High recent operation count", len(recent), "elevated",
            settings.risk_activity_burst_points,
            f"At least {operation_count} {operation_noun} {operation_verb} observed within the screening window.",
        )
    if volume >= settings.risk_transfer_volume_xlm_threshold:
        add_signal(
            "transfer_volume", "High native XLM transfer volume", round(volume, 7), "elevated",
            settings.risk_transfer_volume_points,
            f"Observed native XLM transfer volume reached {settings.risk_transfer_volume_xlm_threshold:g} XLM in the window.",
        )
    if len(counterparties) >= settings.risk_counterparty_min_count:
        add_signal(
            "counterparty_spread", "Broad counterparty spread", len(counterparties), "elevated",
            settings.risk_counterparty_points,
            f"At least {settings.risk_counterparty_min_count} distinct counterparties appeared in observed transfer operations.",
        )
    sequence = account.get("sequence", "0")
    # A low operation sequence is a weak context signal, not a conclusion about legitimacy.
    try:
        seq = int(sequence)
    except (TypeError, ValueError):
        seq = 0
    if seq <= settings.risk_low_sequence_max and recent:
        add_signal(
            "new_account_activity", "Low sequence account with observed activity", seq, "review",
            settings.risk_low_sequence_points,
            f"The account sequence is at most {settings.risk_low_sequence_max}; this alone is not evidence of malicious behavior.",
        )

    assets, trustline_count, native_balance = _asset_context(account)

    score = min(score, 100)
    threshold = settings.risk_high_score_threshold
    return {
        "address": address,
        "score": score,
        "risk_level": "high" if score >= threshold else "elevated" if score >= settings.risk_elevated_score_threshold else "low",
        "threshold": threshold,
        "threshold_exceeded": score >= threshold,
        "scoring_policy_version": settings.risk_policy_version,
        "signals": signals,
        "metrics": {"operations_scanned": len(records), "operations_in_window": len(recent),
                    "transfers_in_window": transfers, "transfer_volume_xlm": round(volume, 7),
                    "distinct_counterparties": len(counterparties), "account_sequence": seq,
                    "native_xlm_balance": round(native_balance, 7), "trustline_count": trustline_count,
                    "window_days": settings.activity_window_days},
        "activity_sample": {
            "operations_scanned": len(records),
            "scan_limit": scan_limit,
            "may_be_incomplete": may_be_incomplete,
        },
        "assets": assets,
        "source": {"horizon_url": _public_endpoint(settings.horizon_url.rstrip("/")), "network": settings.network_passphrase,
                   "observed_at": now.isoformat()},
        "as_of": now.isoformat(),
        "on_chain_action": "none",
    }


def _asset_details(operation: dict, prefix: str = "") -> dict:
    field = lambda suffix: operation.get(f"{prefix}{suffix}")
    asset_type = field("asset_type") or ("native" if not prefix else None)
    asset = {"type": asset_type}
    code = field("asset_code")
    issuer = field("asset_issuer")
    if code is not None:
        asset["code"] = code
    if issuer is not None:
        asset["issuer"] = issuer
    return asset


def _normalize_operation(operation: dict) -> dict:
    op_type = operation.get("type")
    amounts = []
    if op_type in {"path_payment_strict_receive", "path_payment_strict_send"}:
        if operation.get("source_amount") is not None:
            amounts.append({
                "kind": "source",
                "value": str(operation["source_amount"]),
                "asset": _asset_details(operation, "source_"),
            })
        if operation.get("amount") is not None:
            amounts.append({
                "kind": "destination",
                "value": str(operation["amount"]),
                "asset": _asset_details(operation),
            })
    else:
        value = operation.get("amount")
        if value is None and op_type == "create_account":
            value = operation.get("starting_balance")
        if value is not None:
            amounts.append({"kind": "amount", "value": str(value), "asset": _asset_details(operation)})

    return {
        "id": operation.get("id"),
        "type": op_type,
        "created_at": operation.get("created_at"),
        "transaction_hash": operation.get("transaction_hash"),
        "source_account": operation.get("source_account"),
        "from_account": operation.get("from"),
        "to_account": operation.get("to"),
        "amounts": amounts,
    }


def list_account_operations(address: str, limit: int, cursor: str | None = None,
                            settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    horizon_cursor = None
    if cursor:
        try:
            padding = "=" * (-len(cursor) % 4)
            horizon_cursor = base64.urlsafe_b64decode(cursor + padding).decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=422, detail="Invalid operations cursor") from exc
        if not horizon_cursor.isdigit():
            raise HTTPException(status_code=422, detail="Invalid operations cursor")

    params = {"limit": limit, "order": "desc", "include_failed": "false"}
    if horizon_cursor:
        params["cursor"] = horizon_cursor
    url = f"{settings.horizon_url.rstrip('/')}/accounts/{address}/operations"
    payload = _get(url, params=params, settings=settings)
    records = payload.get("_embedded", {}).get("records", [])
    next_cursor = None
    if len(records) == limit and records[-1].get("id") is not None:
        next_cursor = base64.urlsafe_b64encode(str(records[-1]["id"]).encode("ascii")).decode("ascii").rstrip("=")
    return {
        "operations": [_normalize_operation(record) for record in records],
        "next_cursor": next_cursor,
        "limit": limit,
        "source": {"horizon_url": _public_endpoint(settings.horizon_url.rstrip("/")), "network": settings.network_passphrase},
    }


def network_status(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    health = _rpc("getHealth", {}, settings)
    return {"network": settings.network_passphrase, "rpc_url": _public_endpoint(settings.soroban_rpc_url),
            "status": health.get("status", "unknown"), "latest_ledger": health.get("latestLedger"),
            "oldest_ledger": health.get("oldestLedger"),
            "ledger_retention_window": health.get("ledgerRetentionWindow"),
            "observed_at": datetime.now(timezone.utc).isoformat()}


def _symbol_scval(symbol: str) -> str:
    # ScVal XDR: enum SCV_SYMBOL (15), followed by opaque string length and padded bytes.
    raw = symbol.encode("utf-8")
    padded = raw + b"\0" * ((4 - len(raw) % 4) % 4)
    return base64.b64encode((15).to_bytes(4, "big") + len(raw).to_bytes(4, "big") + padded).decode()


def _native(value):
    if isinstance(value, dict):
        for key in ("u32", "u64", "i32", "i64", "address", "symbol", "string"):
            if key in value:
                item = value[key]
                if key == "address" and isinstance(item, dict):
                    return item.get("accountId") or item.get("contractId") or str(item)
                return item
        if "vec" in value:
            return [_native(v) for v in value["vec"] or []]
    return value


def _bytes32_hex(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("bytes")
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return value.lower()
    return None


def _health_ledger(health: dict, field: str) -> int | None:
    """Read a ledger number from getHealth; absent is allowed, garbage is not.

    Before this check a non-numeric value raised TypeError/ValueError, which
    surfaced as an unhandled 500 instead of a stale-ingestion upstream error.
    """
    value = health.get(field)
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise UpstreamDataError("rpc_invalid_response", "Soroban RPC returned an invalid ledger window")


def fetch_flag_event_page(limit: int, cursor: str | None = None,
                          settings: Settings | None = None) -> tuple[list[dict], str | None]:
    settings = settings or get_settings()
    if not settings.contract_id:
        raise HTTPException(status_code=503, detail="CONTRACT_ID is required to read Stellar Sentinel on-chain events")
    params = {
        "filters": [
            {"type": "contract", "contractIds": [settings.contract_id],
             "topics": [[_symbol_scval("flagged"), "*", "*", "**"]]},
            {"type": "contract", "contractIds": [settings.contract_id],
             "topics": [[_symbol_scval("flaggedv2"), "*", "*", "*"]]},
        ],
        "pagination": {"limit": limit},
        "xdrFormat": "json",
    }
    if cursor:
        params["pagination"]["cursor"] = cursor
    else:
        health = _rpc("getHealth", {}, settings)
        latest = _health_ledger(health, "latestLedger")
        requested_start = max(1, (latest or 1) - settings.events_lookback_ledgers)
        oldest = _health_ledger(health, "oldestLedger")
        params["startLedger"] = max(requested_start, oldest) if oldest is not None else requested_start
    result = _rpc("getEvents", params, settings)
    raw_events = result.get("events") or []
    if not isinstance(raw_events, list) or not all(isinstance(event, dict) for event in raw_events):
        raise UpstreamDataError("malformed_page", "Soroban RPC returned malformed events")
    output = []
    for event in raw_events:
        # With xdrFormat="json", Soroban RPC returns topicJson/valueJson, not
        # topic/value; the topic/topics/value fallbacks cover older/other RPCs.
        raw_topics = event.get("topicJson", event.get("topic", event.get("topics", [])))
        topics = [_native(topic) for topic in raw_topics]
        is_v2 = bool(topics) and topics[0] == "flaggedv2"
        raw_value = event.get("valueJson", event.get("value"))
        report_digest = _bytes32_hex(topics[3]) if is_v2 and len(topics) > 3 else None
        if is_v2 and report_digest is None:
            event_logger.warning(
                "event_topic_decode event=malformed_report_digest",
                extra={"event": "malformed_report_digest"},
            )
        output.append({"id": event.get("id"), "ledger": event.get("ledger"),
                       "created_at": event.get("ledgerClosedAt"), "agent": topics[1] if len(topics) > 1 else None,
                       "subject": topics[2] if len(topics) > 2 else None, "score": _native(raw_value),
                       "report_digest": report_digest,
                       "contract_id": event.get("contractId", settings.contract_id), "tx_hash": event.get("txHash")})
    return output, result.get("cursor")


def list_flag_events(limit: int, cursor: str | None = None, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    output, next_cursor = fetch_flag_event_page(limit, cursor, settings)
    return {"events": output, "next_cursor": next_cursor,
            "source": {"rpc_url": _public_endpoint(settings.soroban_rpc_url), "network": settings.network_passphrase,
                       "contract_id": settings.contract_id}}


# Default page budget per sync. Kept as a module constant because callers and
# tests predate EVENT_INGEST_MAX_PAGES; the setting is the source of truth.
_MAX_CATCHUP_PAGES = Settings.model_fields["event_ingest_max_pages"].default

# Soroban RPC cursors are "<19-digit TOID>-<10-digit event index>", which sort
# lexicographically in chain order. Any other provider format is treated as
# opaque and only checked for shape and repetition.
_TOID_CURSOR = re.compile(r"\d{19}-\d{10}")
_OPAQUE_CURSOR = re.compile(r"[A-Za-z0-9._:-]{1,128}")
# JSON-RPC "invalid request" / "invalid params". Our filters are static, so
# when a request carrying a persisted cursor gets one of these, the provider is
# refusing the cursor itself, typically because it fell out of retention.
_CURSOR_REJECTED_CODES = frozenset({-32600, -32602})

_sync_locks: dict[tuple[str, str], threading.Lock] = {}
_sync_locks_guard = threading.Lock()


@dataclass(frozen=True)
class IngestionSyncResult:
    """Outcome of one bounded ``sync_flag_events`` run.

    ``stop_reason`` is one of:

    - ``caught_up``: RPC returned no continuation cursor.
    - ``cursor_stalled``: RPC handed back the cursor it was given.
    - ``partial_page``: a non-empty page shorter than the page size.
    - ``page_budget_exhausted``: ``EVENT_INGEST_MAX_PAGES`` pages were read;
      the next sync resumes from the persisted cursor.
    - ``time_budget_exhausted``: ``EVENT_INGEST_SYNC_BUDGET_SECONDS`` elapsed
      between pages; the next sync resumes from the persisted cursor.
    - ``sync_in_progress``: another sync for this scope is running in this
      process; nothing was fetched.
    - ``cursor_conflict``: another process advanced the cursor first; the
      in-flight page was discarded rather than written over it.
    """

    scope: str
    stop_reason: str
    pages: int = 0
    events: int = 0
    cursor: str | None = None
    cursor_reset: bool = False


def ingestion_scope(settings: Settings) -> str:
    """Cursor and event-identity scope: one per network passphrase and contract."""
    return f"{settings.network_passphrase}:{settings.contract_id}"


def ingestion_retry_delay(consecutive_failures: int, settings: Settings) -> float:
    """Seconds the background indexer waits before its next sync.

    A healthy loop polls every ``EVENT_INGEST_INTERVAL_SECONDS``. Each
    consecutive failure doubles the wait, capped at
    ``EVENT_INGEST_MAX_BACKOFF_SECONDS`` but never shorter than the interval.
    """
    interval = settings.event_ingest_interval_seconds
    if consecutive_failures <= 0:
        return float(interval)
    # Clamp the exponent so a very long outage cannot grow the number unbounded.
    backoff = interval * 2 ** min(consecutive_failures, 32)
    return float(max(interval, min(backoff, settings.event_ingest_max_backoff_seconds)))


def _checked_next_cursor(value, current: str | None, seen: set[str]) -> str | None:
    """Validate RPC's continuation cursor before it is allowed near the store."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _OPAQUE_CURSOR.fullmatch(value):
        raise UpstreamDataError("invalid_cursor", "Soroban RPC returned an invalid event cursor")
    # Revisiting an earlier cursor (A -> B -> A) is a loop, not a stall.
    moved_back = value != current and value in seen
    if current and _TOID_CURSOR.fullmatch(value) and _TOID_CURSOR.fullmatch(current):
        moved_back = moved_back or value < current
    if moved_back:
        raise UpstreamDataError("cursor_regressed", "Soroban RPC event cursor moved backwards")
    return value


def cursor_ledger(cursor: str | None) -> int | None:
    """Ledger sequence encoded in a standard Soroban cursor, else None.

    The TOID packs the ledger into its upper 32 bits. Opaque provider cursors
    carry no position we can trust, so they report no ledger.
    """
    if not cursor or not _TOID_CURSOR.fullmatch(cursor):
        return None
    return int(cursor[:19]) >> 32


def classify_ingestion_failure(exc: BaseException) -> str:
    """Map an exception raised by a sync onto a fixed telemetry label.

    Labels come from ``app.metrics.INGESTION_FAILURES`` and never include
    exception text, URLs, cursors, or provider messages.
    """
    if isinstance(exc, UpstreamDataError):
        return exc.failure
    if isinstance(exc, IngestionCursorRejectedError):
        return "cursor_rejected"
    if isinstance(exc, SorobanRpcError):
        return "rpc_error"
    if isinstance(exc, HTTPException):
        # _rpc chains the transport exception, which separates a slow
        # provider from an unreachable one without parsing messages.
        cause = exc.__cause__
        if isinstance(cause, httpx.TimeoutException):
            return "rpc_timeout"
        if isinstance(cause, httpx.HTTPStatusError):
            return "rpc_http_error"
        if isinstance(cause, httpx.HTTPError):
            return "rpc_unavailable"
        if exc.status_code == 502:
            return "rpc_invalid_response"
        return "upstream_error"
    if isinstance(exc, sqlite3.Error):
        return "store_error"
    return "internal_error"


def _scope_lock(store, scope: str) -> threading.Lock:
    key = (str(Path(store.path).expanduser().resolve()), scope)
    with _sync_locks_guard:
        return _sync_locks.setdefault(key, threading.Lock())


# Indirection so tests can drive the sync time budget without sleeping.
_ingest_clock = time.monotonic


@dataclass
class _SyncProgress:
    """Work committed so far, kept outside the walk so failures can report it."""

    pages: int = 0
    events: int = 0
    cursor_reset: bool = False


def sync_flag_events(store, settings: Settings | None = None) -> IngestionSyncResult:
    """Advance ingestion for the configured scope by a bounded amount of work.

    Soroban RPC's getEvents scans a bounded ledger range per call and returns a
    continuation cursor even when that range holds no matching events, so this
    pages forward, persisting the cursor after every page, until RPC reports
    no further cursor, the cursor stops advancing, a short non-empty page is
    read, or the page or time budget (``EVENT_INGEST_MAX_PAGES``,
    ``EVENT_INGEST_SYNC_BUDGET_SECONDS``) is spent. Full pages keep the walk
    going so a backlog drains without waiting a poll interval per page.

    Only one sync per store and scope runs at a time in a process; a caller
    that finds one already running returns immediately. Across processes,
    every cursor write is a compare-and-set, so a slower worker never moves
    the cursor backwards.

    RPC transport and HTTP failures propagate after the pages already saved
    are committed. A cursor the provider rejects is handled according to
    ``EVENT_INGEST_CURSOR_RECOVERY``. Every outcome, including failures, is
    recorded in ``app.metrics.ingestion_metrics`` and logged with fixed labels.
    """
    settings = settings or get_settings()
    scope = ingestion_scope(settings)
    lock = _scope_lock(store, scope)
    if not lock.acquire(blocking=False):
        ingestion_metrics.record_sync("sync_in_progress", 0, 0, 0.0)
        event_logger.debug(
            "event_ingestion_sync stop_reason=sync_in_progress pages=0 events=0",
            extra={"event": "ingestion_sync", "stop_reason": "sync_in_progress",
                   "pages": 0, "events_read": 0},
        )
        return IngestionSyncResult(scope, "sync_in_progress")

    progress = _SyncProgress()
    started = time.perf_counter()
    try:
        result = _sync_scope(store, settings, scope, progress)
    except Exception as exc:
        _record_failed_sync(exc, progress, _elapsed_ms(started))
        raise
    finally:
        lock.release()

    duration_ms = _elapsed_ms(started)
    ingestion_metrics.record_sync(result.stop_reason, result.pages, result.events,
                                  duration_ms, result.cursor_reset)
    noteworthy = result.events or result.cursor_reset or result.stop_reason in {
        "page_budget_exhausted", "time_budget_exhausted", "cursor_conflict"}
    event_logger.log(
        logging.INFO if noteworthy else logging.DEBUG,
        "event_ingestion_sync stop_reason=%s pages=%s events=%s duration_ms=%s",
        result.stop_reason, result.pages, result.events, duration_ms,
        extra={"event": "ingestion_sync", "stop_reason": result.stop_reason,
               "pages": result.pages, "events_read": result.events,
               "duration_ms": duration_ms, "cursor_reset": result.cursor_reset},
    )
    return result


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _record_failed_sync(exc: BaseException, progress: _SyncProgress, duration_ms: float) -> None:
    failure = classify_ingestion_failure(exc)
    ingestion_metrics.record_failure(failure, progress.pages, progress.events,
                                     duration_ms, progress.cursor_reset)
    status = exc.status_code if isinstance(exc, HTTPException) else None
    extra = {"event": "ingestion_sync_failed", "failure": failure, "status_code": status,
             "pages": progress.pages, "events_read": progress.events,
             "duration_ms": duration_ms, "cursor_reset": progress.cursor_reset}
    # The exception type name is fixed by our code, unlike its message, which
    # can carry provider text or a cursor. Only unexpected errors need it.
    if failure == "internal_error":
        extra["error_type"] = type(exc).__name__
    event_logger.warning(
        "event_ingestion_sync_failed failure=%s status_code=%s pages=%s events=%s duration_ms=%s",
        failure, status, progress.pages, progress.events, duration_ms,
        extra=extra,
    )


def _sync_scope(store, settings: Settings, scope: str, progress: _SyncProgress) -> IngestionSyncResult:
    cursor = store.ingestion_cursor(scope)
    seen = {cursor} if cursor else set()
    page_size = settings.event_ingest_page_size
    deadline = _ingest_clock() + settings.event_ingest_sync_budget_seconds

    def finish(stop_reason: str) -> IngestionSyncResult:
        return IngestionSyncResult(scope, stop_reason, progress.pages, progress.events,
                                   cursor, progress.cursor_reset)

    for attempt in range(settings.event_ingest_max_pages):
        # Always try one page so a tiny budget cannot freeze ingestion; after
        # that, yield and let the next sync resume from the saved cursor.
        if attempt and _ingest_clock() >= deadline:
            return finish("time_budget_exhausted")
        try:
            events, raw_next = fetch_flag_event_page(limit=page_size, cursor=cursor, settings=settings)
        except SorobanRpcError as exc:
            if cursor is None or exc.code not in _CURSOR_REJECTED_CODES:
                raise
            recovery = settings.event_ingest_cursor_recovery
            event_logger.warning(
                "event_ingestion_cursor_rejected rpc_error_code=%s recovery=%s",
                exc.code, recovery,
                extra={"event": "ingestion_cursor_rejected", "rpc_error_code": exc.code,
                       "recovery": recovery},
            )
            # Reset at most once per run: if a fresh start is rejected too,
            # the problem is not the cursor and should surface as a failure.
            if recovery != "reset" or progress.cursor_reset:
                raise IngestionCursorRejectedError(exc.code) from exc
            if not store.reset_ingestion_cursor(scope, cursor):
                return finish("cursor_conflict")
            cursor, seen, progress.cursor_reset = None, set(), True
            continue

        next_cursor = _checked_next_cursor(raw_next, cursor, seen)
        try:
            store.save_page(events, next_cursor, scope, expected_cursor=cursor)
        except CursorConflictError:
            return finish("cursor_conflict")
        except ValueError as exc:
            # A page with an unusable event is rejected whole and the cursor
            # stays put; report it like any other malformed upstream payload.
            raise UpstreamDataError("malformed_page", "Soroban RPC returned a malformed event") from exc
        progress.pages += 1
        progress.events += len(events)

        if next_cursor is None:
            return finish("caught_up")
        if next_cursor == cursor:
            return finish("cursor_stalled")
        seen.add(next_cursor)
        cursor = next_cursor
        if events and len(events) < page_size:
            return finish("partial_page")
    return finish("page_budget_exhausted")
