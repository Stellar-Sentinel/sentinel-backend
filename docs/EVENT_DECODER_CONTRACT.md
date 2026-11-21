# Soroban event-decoder contract

The event decoder accepts Soroban RPC JSON in both current and legacy field
forms while keeping the public event shape stable.

## Accepted shapes

- Read topics from `topicJson`, then fall back to `topic` or `topics`.
- Read values from `valueJson`, then fall back to `value`.
- Normalize `symbol`, scalar numeric values, and nested `address.accountId` or
  `address.contractId` values.
- Decode legacy `flagged` topics without a report digest.
- Decode `flaggedv2` topics only when the digest is exactly 32 bytes represented
  by 64 hexadecimal characters.

## Failure behavior

Unsupported address shapes and malformed digests remain partially decoded with
`null` fields; the decoder never invents a trusted digest. Malformed topic
data emits only a fixed diagnostic label and never logs the RPC payload, URL,
credentials, or signing material.

Tests use deterministic fake RPC responses for legacy, nested-address,
versioned, malformed, and field-fallback cases. They must not call live Horizon
or Soroban endpoints.
