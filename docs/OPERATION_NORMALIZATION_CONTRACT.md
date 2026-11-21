# Horizon operation normalization contract

`GET /accounts/{address}/operations` returns a stable envelope with
`operations`, `next_cursor`, `limit`, and a sanitized source. Each operation
preserves its ID, type, creation time, transaction hash, source/from/to
accounts, and an `amounts` array.

Simple operations use one `amount` entry. Create-account operations map
`starting_balance` to that entry. Strict path payments expose separate source
and destination amounts, each with its own asset identity. Missing optional
values remain absent rather than being invented.

The opaque cursor is a URL-safe encoding of Horizon's numeric ID. Invalid
cursors and unsupported page sizes return 422; short pages return no next
cursor. The normalizer must never expose credentialed upstream URLs.
