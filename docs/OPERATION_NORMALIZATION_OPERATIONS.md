# Horizon operation normalization operations

Use the normalized operations endpoint for descriptive account activity only.
Verify page size and opaque cursor behavior after deployment, and confirm
strict path payments retain separate source and destination asset identities.

Treat malformed upstream records as skipped data and investigate provider
schema changes through logs and fixtures. A 422 indicates a client address,
page-size, or cursor problem; a 404 means Horizon did not find the account;
502/503 indicate upstream availability or response problems.

Do not expose raw Horizon URLs containing credentials, and do not use
operation normalization as an authorization or risk decision by itself.
