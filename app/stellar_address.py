"""Validation helpers for Stellar StrKey account identifiers."""
import base64
import binascii


_CLASSIC_ACCOUNT_VERSION = 48
_MUXED_ACCOUNT_VERSION = 96


def _crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def is_valid_account_id(value: str) -> bool:
    """Return whether value is a checksummed classic or muxed account StrKey."""
    if not isinstance(value, str):
        return False
    if value.startswith("G"):
        expected_length, expected_version = 35, _CLASSIC_ACCOUNT_VERSION
    elif value.startswith("M"):
        expected_length, expected_version = 43, _MUXED_ACCOUNT_VERSION
    else:
        return False

    try:
        padding = "=" * (-len(value) % 8)
        decoded = base64.b32decode(value + padding)
    except (binascii.Error, ValueError):
        return False
    if len(decoded) != expected_length or decoded[0] != expected_version:
        return False

    payload, checksum = decoded[:-2], decoded[-2:]
    return checksum == _crc16_xmodem(payload).to_bytes(2, "little")
