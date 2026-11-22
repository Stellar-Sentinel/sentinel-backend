# Agent authorization lifecycle

The monitoring agent's signing key and its contract authorization are separate
concerns. Possessing `AGENT_SECRET_KEY` does not grant permission to call the
contract.

## Lifecycle

1. Generate a dedicated key and store its secret only in the deployment secret
   manager.
2. Derive the public address offline and have an administrator authorize that
   address as a responder on the intended contract and network.
3. Verify the authorization transaction and contract ID before enabling the
   worker.
4. Run the agent with `AGENT_ENABLED=true`; the worker signs only the
   configured `flag_anomaly_v2` call.
5. On rotation or compromise, disable the worker, revoke the old responder,
   authorize the replacement, verify both transactions, and then re-enable.

The service must fail closed when the secret or contract ID is missing. An
unauthorized submission is a failed transaction, not proof that authorization
was granted. Keep authorization transaction hashes and the active public key in
the deployment record, never the secret key.
