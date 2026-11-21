# Deployment configuration operations

Validate configuration before every deployment and keep the sanitized summary
with the release record. Development defaults may use localhost CORS and omit
an API key; production must use a strong API key and non-local origins.

The deployment record should include environment, network passphrase,
contract-configured state, agent-enabled state, proxy count, and the version of
the configuration schema. It must not include API keys, agent secrets,
credentialed URLs, or full transaction data.

After changing configuration, verify `/health`, `/ready`, and `/network/status`
against the intended network. If the monitoring agent is enabled, separately
verify its derived public address and contract authorization. Roll back on
validation failure instead of starting with partially applied settings.
