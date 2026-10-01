# Proposal: fix-redaction-hardening

## Why

The 2026-09-22 audit (`codeaudit/2026-09-22.md`) empirically confirmed three defects in the §10.7 redaction and
D-13 persistence layer. The opaque-token pattern in `logging_setup.py` redacts every GUID — Cloud PC ids and
`home_account_id`s render as `[REDACTED]`, destroying the log correlation that §9's per-account diagnosis depends
on (F-02, MEDIUM; the audit's bar was "Cloud PC GUIDs must survive; real tokens must not", and the first half
fails). The state store's secret-shape patterns are anchored whole-value, so a token embedded in a longer string —
the realistic accident the control exists for — is persisted without objection (F-11). And running
`python src/winonlinux/app.py` via its own `__main__` block starts the full app with redaction never installed,
because only `winonlinux/__main__.py` calls `configure_logging()` (F-15). A docstring falsehood rides along:
`StateStore.load()` claims "Never raises" while a missing migration step deliberately raises (F-26).

## What Changes

- **GUID carve-out in log redaction** (F-02): canonical 8-4-4-4-12 UUIDs and dotted UUID pairs (`oid.tid`) survive
  redaction, mirroring the carve-out `state_store.py` already implements; genuinely token-shaped runs still die.
- **Unanchored secret detection in the state store** (F-11): JWT-shaped and opaque-token-shaped substrings inside
  longer string values are detected and refused, mirroring `logging_setup`'s unanchored forms.
- **Redaction on every entry path** (F-15): `app.main()` installs `configure_logging()` idempotently (or the
  `app.py` `__main__` block is removed), so no documented way of starting the app runs unredacted.
- **Docstring truth** (F-26): `StateStore.load()`'s contract text matches its deliberate raise-on-missing-migration
  behavior.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `app-foundation`: redaction precision (GUID survival), embedded-secret refusal, redaction-on-all-entry-paths.

## Impact

- Code: `src/winonlinux/logging_setup.py`, `state_store.py`, `app.py`; tests for GUID survival, embedded-token
  refusal, and entry-path coverage.
- Closes audit findings F-02, F-11, F-15, F-26. §10.7 and D-13 are unchanged in intent — this is precision, not
  policy; over-redaction was the safe direction and stays the fallback for ambiguous shapes.
- Independent of all other changes. File and link a `WinOnLinuxCode` Linear issue per repo convention.
