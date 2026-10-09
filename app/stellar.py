"""Read-only clients for Horizon and Stellar RPC."""
from datetime import datetime, timedelta, timezone
import base64
from email.utils import parsedate_to_datetime
import time
import math

import httpx
from fastapi import HTTPException

from app.config import Settings, get_settings

_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


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


def _request_with_retries(method, url: str, settings: Settings, **kwargs) -> httpx.Response:
    retries = settings.upstream_max_retries
    for attempt in range(retries + 1):
        try:
            response = method(url, **kwargs)
        except httpx.TransportError:
            if attempt >= retries:
                raise
            delay = settings.upstream_retry_backoff_seconds * (2 ** attempt)
            time.sleep(min(delay, settings.upstream_retry_after_cap_seconds))
            continue

        if response.status_code in _RETRYABLE_STATUS_CODES and attempt < retries:
            time.sleep(_retry_after_seconds(response, settings, attempt))
            continue
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


def _get(url: str, params: dict | None = None, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    try:
        response = _request_with_retries(
            lambda target, **kwargs: _get_response(target, kwargs.get("params"), settings),
            url,
            settings,
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
            trustline_count += 1
        assets.append({"asset": asset, "balance": str(amount)})

    return assets, trustline_count, native_balance


def _rpc(method: str, params: dict, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    try:
        response = _request_with_retries(
            lambda target, **kwargs: _post_response(target, kwargs.get("json"), settings),
            settings.soroban_rpc_url,
            settings,
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
        raise HTTPException(status_code=502, detail="Soroban RPC returned an error")
    result = payload.get("result", {})
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Soroban RPC returned an invalid result")
    return result


def score_account(address: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    account = _get(f"{settings.horizon_url.rstrip('/')}/accounts/{address}", settings=settings)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=settings.activity_window_days)
    ops_url = f"{settings.horizon_url.rstrip('/')}/accounts/{address}/operations"
    ops = _get(ops_url, {"limit": min(settings.operation_scan_limit, 200), "order": "desc", "include_failed": "false"}, settings)
    embedded = ops.get("_embedded")
    records = embedded.get("records", []) if isinstance(embedded, dict) else []
    if not isinstance(records, list):
        records = []
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
        source, destination = op.get("source_account"), op.get("to")
        for account_id in (source, destination, op.get("from")):
            if account_id and account_id != address:
                counterparties.add(account_id)
        try:
            # Only native XLM amounts are included; asset amounts are never mixed into XLM totals.
            if op.get("type") == "create_account":
                volume += abs(float(op.get("starting_balance", "0")))
            elif op.get("asset_type") in (None, "native"):
                volume += abs(float(op.get("amount", "0")))
        except (TypeError, ValueError):
            pass
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
        add_signal(
            "activity_burst", "High recent operation count", len(recent), "elevated",
            settings.risk_activity_burst_points,
            f"At least {settings.risk_activity_burst_min_operations} operations were observed within the screening window.",
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
        "signals": signals,
        "metrics": {"operations_scanned": len(records), "operations_in_window": len(recent),
                    "transfers_in_window": transfers, "transfer_volume_xlm": round(volume, 7),
                    "distinct_counterparties": len(counterparties), "account_sequence": seq,
                    "native_xlm_balance": round(native_balance, 7), "trustline_count": trustline_count,
                    "window_days": settings.activity_window_days},
        "assets": assets,
        "source": {"horizon_url": settings.horizon_url.rstrip("/"), "network": settings.network_passphrase,
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
        "source": {"horizon_url": settings.horizon_url.rstrip("/"), "network": settings.network_passphrase},
    }


def network_status(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    health = _rpc("getHealth", {}, settings)
    return {"network": settings.network_passphrase, "rpc_url": settings.soroban_rpc_url,
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


def fetch_flag_event_page(limit: int, cursor: str | None = None,
                          settings: Settings | None = None) -> tuple[list[dict], str | None]:
    settings = settings or get_settings()
    if not settings.contract_id:
        raise HTTPException(status_code=503, detail="CONTRACT_ID is required to read Stellar Sentinel on-chain events")
    params = {
        "filters": [{"type": "contract", "contractIds": [settings.contract_id],
                     "topics": [[_symbol_scval("flagged"), "*", "*", "**"]]}],
        "pagination": {"limit": limit},
        "xdrFormat": "json",
    }
    if cursor:
        params["pagination"]["cursor"] = cursor
    else:
        health = _rpc("getHealth", {}, settings)
        latest = int(health.get("latestLedger", 1))
        requested_start = max(1, latest - settings.events_lookback_ledgers)
        oldest = health.get("oldestLedger")
        params["startLedger"] = max(requested_start, int(oldest)) if oldest is not None else requested_start
    result = _rpc("getEvents", params, settings)
    output = []
    for event in result.get("events", []):
        topics = [_native(topic) for topic in event.get("topic", event.get("topics", []))]
        output.append({"id": event.get("id"), "ledger": event.get("ledger"),
                       "created_at": event.get("ledgerClosedAt"), "agent": topics[1] if len(topics) > 1 else None,
                       "subject": topics[2] if len(topics) > 2 else None, "score": _native(event.get("value")),
                       "contract_id": event.get("contractId", settings.contract_id), "tx_hash": event.get("txHash")})
    return output, result.get("cursor")


def list_flag_events(limit: int, cursor: str | None = None, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    output, next_cursor = fetch_flag_event_page(limit, cursor, settings)
    return {"events": output, "next_cursor": next_cursor,
            "source": {"rpc_url": settings.soroban_rpc_url, "network": settings.network_passphrase,
                       "contract_id": settings.contract_id}}


def sync_flag_events(store, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    scope = f"{settings.network_passphrase}:{settings.contract_id}"
    cursor = store.ingestion_cursor(scope)
    events, next_cursor = fetch_flag_event_page(limit=100, cursor=cursor, settings=settings)
    store.save_page(events, next_cursor, scope)
