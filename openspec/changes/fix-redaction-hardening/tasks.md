# Tasks: fix-redaction-hardening

## 1. GUID carve-out in log redaction (F-02)

- [ ] 1.1 In `_redact_text`, protect canonical UUIDs and dotted UUID pairs with placeholders before the
      opaque-token sub, restoring after — same idiom as `state_store.py:66-76` — `logging_setup.py:105`
- [ ] 1.2 Tests: bare GUID and `oid.tid` survive; 40-char undashed run redacted; JWT embedding a UUID-shaped
      segment still fully redacted; `Authorization` header still redacted — `tests/test_logging_setup.py`

## 2. Embedded-secret refusal (F-11)

- [ ] 2.1 Add unanchored `search` variants of the JWT and opaque patterns for string values in `_find_secret`,
      keeping whole-value checks and the UUID carve-out; refusal message names the key path —
      `state_store.py:59,66,101-133`
- [ ] 2.2 Tests: JWT embedded mid-string under key `"data"` → `SecretValueRejected`, file untouched; bare GUID
      still persists — `tests/test_state_store.py`

## 3. Entry-path coverage (F-15)

- [ ] 3.1 `app.main()` calls `configure_logging()` (idempotent) before constructing the application — `app.py`
- [ ] 3.2 Test: `app.main` installs the record factory before `run()` (import-level, no GTK needed for the
      assertion)

## 4. Docstring truth (F-26)

- [ ] 4.1 Correct `StateStore.load()`'s "Never raises" docstring to state the deliberate `KeyError` on a missing
      migration step and possible `OSError` from the migration rewrite — `state_store.py:195-199`

## 5. Sync

- [ ] 5.1 Comment on the `WinOnLinuxCode` Linear issue with the findings closed (F-02, F-11, F-15, F-26)
