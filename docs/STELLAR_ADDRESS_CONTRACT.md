# Stellar address validation contract

Public account identifiers accepted by the API must be valid Stellar StrKey
values. Classic `G...` accounts and muxed `M...` accounts are accepted when
their checksum and payload are valid. Arbitrary strings, malformed lengths,
bad checksums, and unsupported prefixes are rejected before an upstream call.

Validation is syntactic and network-independent; it does not prove that an
account exists or is active on Horizon. Error responses should be generic and
must not echo credentials or untrusted payloads into logs.

Consumers should preserve the original validated identifier when requesting
Horizon data and should treat a 422 validation response separately from a
404/502 upstream account result.
