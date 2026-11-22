# Agent authorization operations

Maintain the monitoring agent's authorization as a deployment record, not as
an implicit property of the secret key.

## Enablement checklist

Record the active network passphrase, contract ID, derived public address, and
the authorization transaction hash. Confirm the contract's responder registry
contains that address before setting `AGENT_ENABLED=true`. The service logs
only non-secret identity context; never copy `AGENT_SECRET_KEY` into tickets,
logs, or shell history.

## Rotation and incident response

For routine rotation, stop the worker, generate a replacement key, authorize
the new public address, verify the authorization transaction, update the secret
and deployment record, then revoke the old responder. For a suspected
compromise, disable the worker first and revoke the old responder before
investigating. Do not re-enable until the replacement address and contract ID
have been independently verified.

An authorization failure should be treated as a configuration or contract
state incident. Preserve the transaction hash and sanitized startup logs, but
do not retry indefinitely or broaden permissions from the application process.
