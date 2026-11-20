# Deployment Configuration Contract

This document defines the configuration boundary for a deployed Sentinel
backend. Settings are loaded from environment variables (with `.env` only for
local development) and validated before the application starts. Validation is
deterministic and does not make network or on-chain calls.

## Configuration sources and privacy

- Environment variables override `.env` values.
- `.env`, signing keys, API keys, and tokens must never be committed.
- Startup diagnostics may report environment, endpoint origins, contract
  presence, and whether the agent is enabled. They must not report secret
  values, full credentials, or signing-key material.
- `get_settings()` is process-cached, so a configuration change requires a
  process restart.

## Invariants

| Setting or relationship | Valid | Invalid | Compatibility impact |
| --- | --- | --- | --- |
| `HORIZON_URL`, `SOROBAN_RPC_URL` | Absolute `http` or `https` URL without embedded credentials | Missing scheme/host, or username/password in the URL | Existing defaults remain unchanged |
| Production transport | Both upstream URLs use `https` | Any production upstream URL uses `http` | Local development may continue to use HTTP test fixtures |
| `NETWORK_PASSPHRASE` | Non-blank network identifier | Blank or whitespace-only value | No ABI or database migration |
| `CONTRACT_ID` | Empty for a read-only development deployment, or a 56-character Stellar contract ID beginning with `C` | Malformed non-empty value; missing in production or when the agent is enabled | `/events` remains unavailable without a contract in development |
| `ENVIRONMENT=production` | API key is at least 32 characters and CORS excludes localhost/loopback | Missing/short API key or local CORS origin | Production deployments fail closed at startup |
| `AGENT_ENABLED=true` | A contract ID and a valid Stellar secret seed are present | Either value is missing or malformed | No live calls are made by validation |
| `AGENT_SECRET_KEY` | Kept out of diagnostics and source control | Exposed in logs, examples, or committed files | Secret rotation still requires a restart |

## Examples

### Valid local read-only configuration

```dotenv
ENVIRONMENT=development
HORIZON_URL=https://horizon-testnet.stellar.org
SOROBAN_RPC_URL=https://soroban-testnet.stellar.org
NETWORK_PASSPHRASE=Test SDF Network ; September 2015
CONTRACT_ID=
AGENT_ENABLED=false
AGENT_SECRET_KEY=
```

### Valid production configuration

```dotenv
ENVIRONMENT=production
HORIZON_URL=https://horizon.example
SOROBAN_RPC_URL=https://rpc.example
NETWORK_PASSPHRASE=Test SDF Network ; September 2015
CONTRACT_ID=CCAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
API_KEY=<at-least-32-character-secret>
CORS_ORIGINS=https://dashboard.example
AGENT_ENABLED=false
```

### Invalid configurations

```dotenv
# Rejected: production upstream traffic must be encrypted.
ENVIRONMENT=production
HORIZON_URL=http://horizon.example

# Rejected: the enabled signer must have both dependencies.
ENVIRONMENT=production
AGENT_ENABLED=true
CONTRACT_ID=
AGENT_SECRET_KEY=
```

## Failure and deployment behavior

The service must reject invalid values during settings construction, before
the HTTP server or background workers start. Error messages identify the
setting or invariant, but never include the value of a secret. A deployment
platform should treat this startup failure as a configuration error, correct
the environment, and restart the process.

The implementation does not change Stellar contract interfaces, event schemas,
or storage layout. It only rejects configurations that could otherwise send
requests to the wrong network or enable a signer without the resources needed
to operate safely.
