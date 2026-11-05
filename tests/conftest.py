"""Force a safe, deterministic test environment regardless of a local .env.

app.main caches one `Settings` instance at import time (`get_settings` is
lru_cache'd), so a contributor's real .env — e.g. with the monitoring agent
enabled — would otherwise make tests spin up live background tasks against
real Horizon/Soroban RPC, slowing or destabilizing the suite and risking a
real on-chain submission during a test run. These must be set before any
`app.*` module is imported by a test file.
"""
import os

os.environ["AGENT_ENABLED"] = "false"
os.environ["AGENT_SECRET_KEY"] = ""
os.environ["CONTRACT_ID"] = ""
