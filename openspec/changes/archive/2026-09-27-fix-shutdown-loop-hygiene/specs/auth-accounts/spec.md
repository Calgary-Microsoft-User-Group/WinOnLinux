# auth-accounts — cache-write serializer loop affinity

Fixes the import-time `asyncio.Lock` the 2026-09-22 audit's Python 3.12 suite run exposed (finding F-09; spec.md
§6.3 concurrency).

## MODIFIED Requirements

### Requirement: Cache mutations are serialized by a loop-affine lock

Every MSAL call that can mutate the token cache (interactive acquisition, silent acquisition, `remove_account`)
SHALL remain serialized behind one lock per token cache — unchanged from `add-auth-account-manager` — but that
lock SHALL be owned by the `AuthManager` instance (created in `__init__`, dying with the manager) rather than
created at module import, so it can never bind to an event loop other than the one the manager runs on.

#### Scenario: Serialization preserved

- **WHEN** two accounts' acquisitions run concurrently on the manager's loop
- **THEN** their cache-mutating MSAL calls execute strictly serially, as before

#### Scenario: No cross-loop binding

- **WHEN** two `AuthManager` instances are created under two different event loops (as the unit suite does per
  test)
- **THEN** each serializer binds only to its own loop and no cross-loop `RuntimeError` occurs
