# Soroban event topic decoding

The event reader requests Soroban RPC events in JSON format and accepts both
the current `topicJson`/`valueJson` names and the legacy `topic`/`value` names.
This keeps the reader compatible with providers that expose the older field
names without changing the public event response.

## Topic contract

The first topic is a symbol:

- `flagged` is the legacy event shape: topics identify the agent and subject;
  the value is the score and no report digest is present.
- `flaggedv2` is the versioned shape: topics identify the agent, subject, and a
  32-byte report digest; the value is the score.

Address topics may be bare strings or JSON address objects containing
`accountId` or `contractId`. The decoder preserves the normalized address
string and ignores unsupported shapes rather than guessing.

## Digest validation

Only exactly 64 hexadecimal characters are accepted as a `flaggedv2` digest.
The value is normalized to lowercase. Missing, short, long, or non-hex values
become `null` in the public event response; they are not silently represented
as a trusted digest. The score and event metadata remain available so callers
can inspect the malformed event without treating its evidence as complete.

## Compatibility and failure behavior

The decoder is read-only and never makes a live-chain call beyond the single
RPC page requested by the caller. Tests use fake RPC payloads for legacy,
versioned, malformed, and nested-address forms. A provider field rename is
handled by the fallback lookup, while an unknown topic shape remains visible
as a partially decoded event with a null digest.
