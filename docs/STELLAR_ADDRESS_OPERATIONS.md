# Stellar address operations

Validate account identifiers at the API boundary before calling Horizon.
Accept only checksummed classic `G...` or muxed `M...` StrKey values; reject
malformed input with 422 and do not make an upstream request.

Validation does not confirm account existence. Treat a valid identifier with a
404 from Horizon separately from invalid input, and treat upstream failures as
operational errors. Preserve the validated value when constructing upstream
URLs and keep it out of logs unless an approved privacy-safe correlation
workflow requires it.
