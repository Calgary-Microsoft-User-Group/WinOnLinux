# app-foundation — redaction precision and coverage

Refines the §10.7 redaction and D-13 secret-refusal heuristics found imprecise by the 2026-09-22 audit (F-02,
F-11, F-15, F-26).

## ADDED Requirements

### Requirement: Redaction preserves resource identifiers while removing token shapes

Log redaction SHALL preserve canonical UUIDs (8-4-4-4-12) and dotted UUID pairs (`oid.tid` home account ids) so
per-account and per-resource log correlation survives (§9 diagnosis), while continuing to redact JWT-shaped runs,
`Authorization` header values, and non-UUID opaque runs of 32+ token characters. A token that merely contains a
UUID-shaped substring SHALL still be redacted in full.

#### Scenario: GUIDs survive

- **WHEN** a log message contains a Cloud PC GUID and a `home_account_id` (`oid.tid`)
- **THEN** both appear verbatim in the emitted record

#### Scenario: Tokens still die

- **WHEN** a log message contains a JWT or a 40-character undashed base64url run
- **THEN** the emitted record shows `[REDACTED]` for it, including when the JWT embeds a UUID-shaped segment

### Requirement: Embedded secrets are refused by the state store

Secret-shape detection SHALL match JWT-shaped and opaque-token-shaped substrings anywhere inside string values,
not only whole values, and refusal SHALL name the offending key path. Canonical bare UUIDs remain persistable.

#### Scenario: Token concatenated into a diagnostic string

- **WHEN** `save()` receives a value like `"note: <JWT>"` under an innocuous key
- **THEN** the write is refused with `SecretValueRejected` and the file on disk is untouched

### Requirement: Redaction is installed on every application entry path

Every documented way of starting the application SHALL install the redaction record factory before any component
logs. Direct execution of `app.py` and `python -m winonlinux` SHALL be equivalent in this respect (§10.7
"enforced structurally").

#### Scenario: Direct module execution

- **WHEN** the app is started via `app.py`'s own `__main__` block
- **THEN** the record factory is installed before `run()` and no log line is emitted unredacted
