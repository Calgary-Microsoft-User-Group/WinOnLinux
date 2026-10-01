# Design: add-state-change-listeners

## Context

The observer pattern already exists in this codebase — `AuthManager.add_active_account_listener` fires
synchronously on the loop thread and `app.py` consumes it — so this change extends an established idiom to the two
state sources that lack it, rather than introducing an event bus. Everything runs on D-18's single asyncio loop,
which GTK shares via the bridge: a listener called synchronously from provider/auth code *is already on the GTK
thread*, so no `GLib.idle_add` marshalling layer is needed.

## Goals / Non-Goals

**Goals:**

- Every state a §4.4 UI element renders (offline banner, per-account ReauthRequired banner, DEVICE_CA_BLOCKED
  notice, status chips, skeleton/loading) is derivable from a subscription, with no UI-side polling timer.
- One mapping table owns user-facing error text; raw codes reach logs only.
- The store I/O contract is written down before the first GTK caller exists.

**Non-Goals:**

- No GObject signals or properties on backend objects — backend stays PyGObject-free (testable without GTK,
  §13.4); the UI shell may wrap listeners into GObject notifications in its own layer.
- No event replay/queue: a subscriber reads current state (`accounts[*].state`, per-account last result) at
  subscribe time, then receives transitions. No history is retained.
- No localization framework — `user_message` is English source text; i18n is a later, separate concern.
- No async listener support: callbacks are plain callables; anything slow schedules its own task.

## Decisions

- **Listener signature `(home_account_id: str, payload)`** — `AuthState` for auth, `EnumerationResult` for
  enumeration — matching `add_active_account_listener`'s style; `remove_*_listener` counterparts for symmetry.
  Exceptions raised by a listener are caught and logged, never allowed to corrupt the state transition that fired
  them.
- **Auth transitions fire from `AccountAuthState` via an injected callback,** not from scattered `AuthManager`
  call sites — the state object is the single place every transition already passes through (`_log_transition`),
  so notification cannot drift from reality. DEVICE_CA_BLOCKED consequently fires exactly once (the F-14 guards
  from `fix-account-lifecycle` prevent re-entry; that change should land first or together).
- **Provider notifies on every outcome including `Failed`,** keeping `last_result` untouched by failures. The
  listener is the channel for "refreshes are failing"; the stored result remains "last known good" (FR-1-AC-4).
- **`user_message` on the error/result types, populated from one module-level table** keyed by type (plus the
  §9-required specials: proxy-named message for `GraphProxyError`, no-licence wording, keyring refusal). MSAL's
  `error_description` is renamed `raw_error_description_for_logs`. A test asserts no `user_message` contains
  `AADSTS`.
- **Store convention over async facade:** document "call via `run_blocking` from the loop thread" on
  `StateStore.load/save` and `BookmarkStore.load`, and have `app.py`'s existing call sites comply. An async facade
  was rejected for now: two call sites exist, and the facade would duplicate every method for no behavioral gain
  before the UI lands.

## Risks

- Listener exceptions being swallowed could hide UI bugs — mitigated by logging at WARNING with the listener's
  qualified name.
- If `fix-account-lifecycle` does not land first, the auth listener will faithfully report the F-14/F-19 contract
  gaps (e.g. no SILENT_REFRESH transitions to observe). Sequencing noted in both proposals.
