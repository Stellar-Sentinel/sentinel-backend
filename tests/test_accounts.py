import base64

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import stellar
from app.main import app


client = TestClient(app)
ADDRESS = "G" + "A" * 55


def test_account_operations_normalize_assets_and_paginate(monkeypatch):
    calls = []
    records = [
        {
            "id": "12345",
            "type": "path_payment_strict_send",
            "created_at": "2026-10-08T10:00:00Z",
            "transaction_hash": "tx-hash",
            "source_account": ADDRESS,
            "from": ADDRESS,
            "to": "G" + "B" * 55,
            "source_amount": "2.5",
            "source_asset_type": "native",
            "amount": "5.0",
            "asset_type": "credit_alphanum4",
            "asset_code": "USDC",
            "asset_issuer": "G" + "C" * 55,
        }
    ]

    def fake_get(url, params=None, settings=None):
        calls.append((url, params))
        return {"_embedded": {"records": records}}

    monkeypatch.setattr(stellar, "_get", fake_get)
    first = client.get(f"/accounts/{ADDRESS}/operations?limit=1")

    assert first.status_code == 200
    payload = first.json()
    operation = payload["operations"][0]
    assert operation["id"] == "12345"
    assert operation["transaction_hash"] == "tx-hash"
    assert operation["from_account"] == ADDRESS
    assert operation["to_account"] == "G" + "B" * 55
    assert operation["amounts"] == [
        {"kind": "source", "value": "2.5", "asset": {"type": "native"}},
        {
            "kind": "destination",
            "value": "5.0",
            "asset": {
                "type": "credit_alphanum4",
                "code": "USDC",
                "issuer": "G" + "C" * 55,
            },
        },
    ]
    assert payload["next_cursor"] == base64.urlsafe_b64encode(b"12345").decode().rstrip("=")
    assert calls[0][0].endswith(f"/accounts/{ADDRESS}/operations")

    next_page = client.get(
        f"/accounts/{ADDRESS}/operations?limit=1&cursor={payload['next_cursor']}"
    )
    assert next_page.status_code == 200
    assert calls[1][1]["cursor"] == "12345"


def test_account_operations_normalize_native_payment_and_short_page(monkeypatch):
    monkeypatch.setattr(stellar, "_get", lambda *args, **kwargs: {
        "_embedded": {"records": [{
            "id": "12",
            "type": "payment",
            "created_at": "now",
            "transaction_hash": "hash",
            "source_account": ADDRESS,
            "to": "G" + "D" * 55,
            "amount": "7.25",
            "asset_type": "native",
        }]}
    })

    response = client.get(f"/accounts/{ADDRESS}/operations?limit=20")

    assert response.status_code == 200
    assert response.json()["operations"][0]["amounts"] == [
        {"kind": "amount", "value": "7.25", "asset": {"type": "native"}}
    ]
    assert response.json()["next_cursor"] is None


def test_account_operations_reject_invalid_address_page_size_and_cursor():
    assert client.get("/accounts/not-an-address/operations").status_code == 422
    assert client.get(f"/accounts/{ADDRESS}/operations?limit=101").status_code == 422
    assert client.get(f"/accounts/{ADDRESS}/operations?cursor=not-a-cursor").status_code == 422


def test_account_operations_preserve_horizon_errors(monkeypatch):
    def not_found(*args, **kwargs):
        raise HTTPException(status_code=404, detail="Stellar account was not found on the configured network")

    monkeypatch.setattr(stellar, "_get", not_found)
    missing = client.get(f"/accounts/{ADDRESS}/operations")
    assert missing.status_code == 404

    def bad_gateway(*args, **kwargs):
        raise HTTPException(status_code=502, detail="Horizon request failed")

    monkeypatch.setattr(stellar, "_get", bad_gateway)
    failed = client.get(f"/accounts/{ADDRESS}/operations")
    assert failed.status_code == 502
