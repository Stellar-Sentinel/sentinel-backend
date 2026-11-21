# Agent cooldown and duplicate-flag contract

The monitoring worker must not repeatedly flag the same account during a
single cooldown window. Candidate selection is also idempotent within a poll:
duplicate source accounts are reduced to their first, most-recent observation.

## Invariants

- the agent's own public address is never a candidate;
- one account is scored at most once per cycle;
- an account below the high-risk threshold is not submitted;
- a cooldown starts only after a flag submission succeeds;
- a scoring, simulation, submission, or confirmation failure does not create a
  successful cooldown record;
- cooldown state is bounded to the process and is reset on restart;
- a cooldown never changes the stored event history or the canonical report
  digest.

The default cooldown is one hour and must remain bounded by configuration. A
future persistent deduplication layer must key records by network, contract,
agent, subject, and canonical digest, and must define how pending and failed
transactions expire. Until then, transaction hashes and sanitized logs are the
source of truth for investigating duplicates.
