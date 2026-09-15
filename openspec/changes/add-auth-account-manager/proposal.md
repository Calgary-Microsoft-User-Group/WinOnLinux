# Proposal: add-auth-account-manager

## Why

Every feature in the product — enumeration, web launch, management actions, and eventually the native path — is
scoped to a signed-in Entra ID account and gated on a fresh token (FR-4-AC-1). The Auth/Account Manager of §5.1 is
therefore the first functional layer above the app foundation: nothing else in Phase 0 can be built against a live
tenant until interactive sign-in, the per-account token cache, and the token expiry state machine (§6.4) exist.

## What Changes

- Implement OAuth 2.0 authorization code flow with PKCE as a **public client** via **MSAL Python** (D-17), with the
  system browser and a loopback redirect bound to `127.0.0.1` on an **ephemeral port** (D-6, §10.3): `state`
  validated, a single response accepted, listener timeout enforced.
- Implement the per-account token cache backed by the **OS keyring via `msal-extensions`** (D-2, D-17, §6.3).
  A keyring is **required**; where no Secret Service provider exists the app refuses sign-in with a plain
  explanation — there is no encrypted-file fallback.
- Implement **multi-account support** keyed by home account ID (FR-3, all five acceptance criteria): add account,
  switch active account with cancellation of the previous account's in-flight work, per-account sign-out, cache
  enumeration at startup.
- Implement the **token expiry state machine** of §6.4 per account (SignedOut / InteractiveAuth / Active /
  SilentRefresh / ReauthRequired / Offline) satisfying FR-4-AC-1 … AC-7, including claims challenges passed
  **verbatim** with bounded retries, and Offline (not ReauthRequired) on network failure.
- Implement **single-flight silent refresh per account**, serialized cache writes, and clock-skew-tolerant expiry
  evaluation (§6.3). Whether `msal-extensions` supplies cross-process locking on Linux is Gate STACK verification
  **V1** — until verified, the application supplies single-flight itself.
- Implement **device-state Conditional Access detection** (§6.5): the `AADSTS53000` family maps to a dedicated
  explanatory state, never `ReauthRequired`, never retried.
- Implement the **scope/consent strategy** of §6.2: `CloudPC.Read.All` requested at first sign-in,
  `CloudPC.ReadWrite.All` via incremental consent, and the guided admin-consent surface for unconsented tenants.

## Capabilities

### New Capabilities

- `auth-accounts`: Entra ID sign-in, per-account keyring-backed token cache, multi-account lifecycle, the token
  expiry state machine, single-flight refresh, consent strategy, and device-state Conditional Access detection.

### Modified Capabilities

_None — this is the first specification of this capability._

## Impact

- New Auth/Account Manager component (§5.1); every provider (Graph, feed, launchers) consumes its
  `acquire_token_silently(account)` surface.
- New runtime dependencies: `msal`, `msal-extensions`, and the Secret Service via the keyring (libsecret/KWallet).
- Depends on `add-app-foundation` (asyncio loop D-18, single-instance FR-4-AC-7, XDG state store D-13, logging
  redaction §10.7).
- Requires the multi-tenant Entra app registration (D-19) with a **native public-client loopback redirect — never
  `spa`** (§6.1, risk 6). Registration creation is Stage 0 / Sprint work, not part of this change.
- Unverified items this change must not paper over: V1 (`msal-extensions` Linux locking — can fire D-17's revisit
  trigger) and the exact device-state `AADSTS` code set (confirmed at Stage 0, §6.5).
