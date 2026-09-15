# Design: add-auth-account-manager

## Context

The stack is decided: Python 3 + GTK4/libadwaita (D-16), MSAL Python with `msal-extensions` (D-17), a single asyncio
event loop with per-account task groups (D-18). §6 specifies authentication at the OAuth 2.0 protocol level; this
design maps it onto MSAL Python. The app registration (D-19, owned by Kevin Kaminski) is multi-tenant with a
native public-client loopback redirect. Status honesty: the protocol flow and MSAL API shapes are **verified**
(documented, stable); `msal-extensions` keyring/locking behavior on Linux is **decided but unverified** (V1);
the exact `AADSTS` device-state code set is **assumed** pending Stage 0 confirmation.

## Goals / Non-Goals

**Goals:**

- Interactive and silent token acquisition for any number of accounts, satisfying FR-3 and FR-4 in full.
- The §6.4 state machine as an explicit, unit-testable per-account object (synthesized-response tests, §13.2).
- Single-flight refresh and serialized cache writes regardless of what the library provides (§6.3).
- Honest terminal states: no-keyring refusal (D-2), device-CA explanation (§6.5), guided admin consent (§9).

**Non-Goals:**

- Broker integration / device-bound SSO (D-10 — committed, scheduled after Phase 1).
- The AVD feed token audience acquisition path beyond exposing `acquire_token_silently(account, scopes)` — the feed
  provider change owns feed specifics; Stage 0 owns whether the audience is grantable at all.
- Creating the Entra app registration itself (Stage 0, D-19).
- PAC-script / authenticated-proxy support (§5.9 — deferred).

## Decisions

- **MSAL `PublicClientApplication` per process, one instance, shared token cache.** Accounts are distinguished via
  MSAL's account objects; we key all app state by `home_account_id` (FR-3-AC-4). Alternative — one MSAL app per
  account — rejected: the MSAL cache schema is already multi-account and one instance keeps single-flight tractable.
- **Loopback listener implemented with asyncio on `127.0.0.1`, port 0 (ephemeral, D-6).** Accepts exactly one
  response, validates `state`, times out (default 300 s), then closes. MSAL's built-in interactive flow is used only
  if it can be constrained to these rules; otherwise we drive `initiate_auth_code_flow` /
  `acquire_token_by_auth_code_flow` with our own listener. (MSAL's own listener behavior on ephemeral ports is a V1
  companion check.)
- **App-level single-flight, always.** Per account: an asyncio lock plus a shared in-flight future — concurrent
  silent acquisitions await the same result (FR-4-AC-6). Cache writes go through one serialized writer per account.
  We do this even if `msal-extensions` proves to lock correctly, because V1 is unverified and the cost is small;
  if V1 *fails*, D-17's revisit trigger fires and this app-level layer is already the specified fallback (§6.3).
- **Cross-process safety via single-instance enforcement** (FR-4-AC-7, supplied by app foundation's GTK application
  uniqueness), not a cross-process lock. If single-instance is ever relaxed, §6.3 makes a cross-process lock
  mandatory — recorded here so the coupling is visible.
- **State machine as data, not control flow.** A per-account `AuthState` enum with an explicit transition table
  mirroring §6.4, driven by classified MSAL errors: `invalid_grant` → ReauthRequired; `interaction_required` +
  `claims` → InteractiveAuth carrying `claims` verbatim; network errors → retry/backoff then Offline while an
  unexpired AT exists; `AADSTS53000` family → DeviceCABlocked (a distinct terminal state outside §6.4's cycle,
  per §6.5). Error classification is a pure function → unit-testable with synthesized responses.
- **Expiry evaluated with a safety margin** (default 5 min) rather than exact `exp` (§6.3 clock-skew rule).
- **Consent strategy:** first sign-in requests `openid profile offline_access CloudPC.Read.All`;
  `CloudPC.ReadWrite.All` is requested incrementally on first management action. A `403`/consent error surfaces the
  guided admin-consent flow (admin-consent URL composed for forwarding, §9) rather than a generic error.

## Risks / Trade-offs

- [V1: `msal-extensions` keyring persistence or locking misbehaves on Linux/Flatpak] → App-level single-flight and
  serialized writes are unconditional; if persistence itself fails, D-17's revisit trigger fires and the cache layer
  is replaced behind the same interface. Verification is scheduled at Gate STACK, before Stage 2 code.
- [No Secret Service provider at runtime (common under vanilla Flatpak)] → D-2 refusal state: plain explanation, no
  sign-in, no weaker fallback. The Flatpak manifest change owns granting the Secret Service portal (V2).
- [Registration mistakenly created as `spa` caps refresh tokens at 24 h (risk 6)] → Registration checklist in the
  Stage 0 issue; this change asserts at runtime only what it can observe (unexpectedly short RT lifetimes are logged
  at debug, never "handled").
- [Claims-challenge retry loop (risk 8)] → Bounded retry counter per request chain; a test asserts no unmodified
  retry of a claims-challenged request (FR-4-AC-4).
- [Device-CA tenants misread as auth failures (risk 10)] → Dedicated DeviceCABlocked state with explanatory copy;
  never `ReauthRequired`, never retried (§6.5). Exact code set confirmed at Stage 0.

## Open Questions

- V1 (Gate STACK): does `msal-extensions` on Linux provide working libsecret persistence and cross-process locking?
- Stage 0 sub-task (D-6): does Entra's loopback redirect matching accept an ephemeral port without a registered
  fixed port? (Documented behavior says yes for `http://127.0.0.1`; confirm empirically.)
- Exact current `AADSTS` device-state code set (§6.5) — confirm at Stage 0.
