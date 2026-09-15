# auth-accounts — Entra ID sign-in, token lifecycle, and multi-account management

Implements spec.md §6 (identity and session lifecycle), FR-3 (multiple accounts), FR-4 (token expiration), and the
related security controls §10.2/§10.3. Decision IDs cited are normative entries in spec.md §14.

## ADDED Requirements

### Requirement: Interactive sign-in uses OAuth 2.0 auth code + PKCE with a hardened loopback redirect

The application SHALL sign accounts in using the OAuth 2.0 authorization code flow with PKCE against Microsoft
identity platform v2 endpoints, as a public client with no client secret (§6.1). The redirect listener SHALL bind
`127.0.0.1` only, on an ephemeral port (D-6), SHALL validate `state`, SHALL accept a single response, and SHALL
time out (§10.3).

#### Scenario: Successful first sign-in

- **WHEN** the user chooses "Add account…" and completes authentication in the system browser
- **THEN** the account enters the Active state with access, refresh and ID tokens cached, and appears in the
  account switcher

#### Scenario: Loopback listener rejects a second or forged response

- **WHEN** the listener has already consumed one redirect response, or receives a response whose `state` does not
  match the outstanding request
- **THEN** the response is rejected and no token exchange is attempted with its contents

#### Scenario: User cancels interactive auth

- **WHEN** the user abandons or cancels the browser sign-in
- **THEN** the application returns to its prior state with no error dialog (§9)

### Requirement: Token cache is keyring-backed and keyring is required

Per-account tokens (access, refresh, ID, account object) SHALL be persisted only in the OS keyring-backed encrypted
cache (§6.3, §10.2). Where no Secret Service provider is available the application SHALL report that plainly and
refuse sign-in; there SHALL be no encrypted-file or plaintext fallback (D-2).

#### Scenario: Keyring available

- **WHEN** an account signs in on a system with a functioning Secret Service provider
- **THEN** tokens are persisted via the keyring and never written to plain files, logs, or environment variables

#### Scenario: Keyring absent

- **WHEN** no Secret Service provider is available at sign-in time
- **THEN** the application states that a keyring is required and does not sign in, offering no weaker storage mode

### Requirement: Multiple accounts are isolated and keyed by home account ID

The application SHALL support any number of simultaneously signed-in accounts, each keyed by home account ID, with
per-account token cache entries and auth state (FR-3). Adding an account SHALL NOT invalidate, re-prompt, or
disturb any existing account (FR-3-AC-1). The same user in two tenants SHALL appear as two independent accounts
(FR-3-AC-4). At startup the application SHALL rebuild the account list from the cache (§6.3).

#### Scenario: Second account added without disturbing the first

- **WHEN** a second account signs in while the first holds cached tokens
- **THEN** both accounts hold independent cache entries and the first account's tokens remain valid and untouched

#### Scenario: Same user in two tenants

- **WHEN** one person signs in from two different tenants
- **THEN** two accounts exist with independent state, keyed by their distinct home account IDs

#### Scenario: Startup rebuild

- **WHEN** the application starts with two accounts in the cache
- **THEN** the account switcher lists both without any interactive prompt

### Requirement: Exactly one account is active and switching cancels the previous account's work

Exactly one account SHALL be active at a time; all outbound calls are scoped to it (FR-3). Switching SHALL cancel
in-flight enumeration and pending actions belonging to the previous account within the NFR-2 budget (FR-3-AC-2),
without disturbing other accounts' cached tokens or live sessions.

#### Scenario: Switch cancels in-flight work

- **WHEN** the user switches from account A to account B while A's enumeration is in flight
- **THEN** A's in-flight requests are cancelled, B's resource context loads within the NFR-2 budget, and A's cached
  tokens and any live sessions are unaffected

### Requirement: Per-account sign-out

Signing out an account SHALL remove that account's tokens from the cache and its resources from the UI, leaving
every other account functional without re-authentication (FR-3-AC-3).

#### Scenario: Sign out one of two accounts

- **WHEN** account A is signed out while account B is signed in
- **THEN** A's cache entries are deleted and A's resources disappear, and B continues to enumerate with no
  re-authentication

### Requirement: Silent acquisition precedes every outbound call

Every Graph call, feed query, and connection launch SHALL be immediately preceded by a silent token acquisition for
the owning account; no code path SHALL derive refresh timing from an assumed token lifetime (FR-4-AC-1, §6.4).
Expiry SHALL be evaluated with a safety margin rather than against exact `exp` (§6.3).

#### Scenario: Acquisition precedes the call

- **WHEN** any component issues an outbound Graph or feed request
- **THEN** an instrumented test observes a silent acquisition for that account immediately before the request, and
  no scheduled refresh timer exists anywhere in the codebase

### Requirement: Silent refresh is invisible and persists the rotated refresh token

An expired access token with a valid refresh token SHALL refresh with zero user-visible interaction, and the
rotated refresh token SHALL be persisted before the dependent call proceeds (FR-4-AC-2, §6.3).

#### Scenario: Transparent refresh

- **WHEN** a silent acquisition finds the access token expired but the refresh token valid
- **THEN** the token refreshes with no UI interaction and the new refresh token is persisted before the original
  call continues

### Requirement: Single-flight refresh per account with serialized cache writes

Concurrent silent acquisitions for one account SHALL collapse into exactly one network refresh whose result is
shared by all waiters, and refresh-token writes SHALL be atomic per account (FR-4-AC-6, §6.3). The application
SHALL supply this itself; it SHALL NOT be assumed from the MSAL flavor until Gate STACK verification V1 confirms
library behavior (§14.4). A concurrent-refresh collision is a defect, not a condition to handle (§9).

#### Scenario: N simultaneous acquisitions

- **WHEN** N simultaneous silent acquisitions are issued against one account holding an expired access token
- **THEN** exactly one network refresh occurs and exactly one valid rotated refresh token survives in the cache

### Requirement: ReauthRequired isolates to the failing account

`invalid_grant`, refresh-token expiry, or revocation SHALL move only the affected account to ReauthRequired, shown
as a non-blocking per-account banner whose inline button starts interactive auth with `login_hint` prefilled
(FR-4-AC-3, §6.4). Every other account SHALL stay fully functional, including live sessions (FR-3-AC-5).

#### Scenario: Server-side revocation

- **WHEN** account A's refresh token is revoked server-side and its next silent acquisition returns `invalid_grant`
- **THEN** only A enters ReauthRequired with its banner, and account B's enumeration and sessions continue
  unaffected

#### Scenario: Re-auth from the banner

- **WHEN** the user clicks the ReauthRequired banner's button
- **THEN** interactive authentication starts for that account with `login_hint` prefilled with its UPN

### Requirement: Claims challenges are honored verbatim with bounded retries

A Conditional Access / CAE claims challenge (`interaction_required` with `claims`) SHALL trigger interactive
re-authentication carrying the returned `claims` parameter verbatim. The application SHALL never issue an
unmodified retry of a claims-challenged request, and retry attempts SHALL be bounded (FR-4-AC-4, §6.4, risk 8).

#### Scenario: Claims challenge round-trip

- **WHEN** a Graph call fails with a claims challenge
- **THEN** the resulting interactive request carries the returned `claims` verbatim, and a test asserts no
  unmodified retry occurred and the retry count is bounded

### Requirement: Network failure yields Offline, not ReauthRequired

Network failure during silent refresh SHALL yield the Offline state with retry/backoff; an unexpired cached access
token SHALL continue to be used (FR-4-AC-5, §6.4).

#### Scenario: Refresh attempted while offline

- **WHEN** silent refresh fails with a network error and a cached unexpired access token exists
- **THEN** the account is treated as Offline (never ReauthRequired) and the cached access token continues to serve
  requests until connectivity returns

### Requirement: Device-state Conditional Access is detected and explained, never retried

Entra device-state errors (the `AADSTS53000`/`53001`/`530003` family; exact set confirmed at Stage 0) SHALL map to
a dedicated state that names the cause — the tenant requires a managed/compliant device this client cannot satisfy,
and the web client on a compliant device is the available route (§6.5). The account SHALL NOT enter ReauthRequired
and the request SHALL NOT be retried.

#### Scenario: Device-CA-enforcing tenant

- **WHEN** sign-in or token acquisition fails with a device-state error code
- **THEN** the UI presents the dedicated explanation naming the tenant policy as the cause, with no retry loop and
  no ReauthRequired banner

### Requirement: Consent is requested incrementally and unconsented tenants get the guided flow

First sign-in SHALL request `openid profile offline_access CloudPC.Read.All`; `CloudPC.ReadWrite.All` SHALL be
requested via incremental consent when the user first invokes a management action (§6.2). A consent-missing error
SHALL surface the guided admin-consent flow — an explanation that a tenant admin must approve, with the
admin-consent URL available for forwarding (§9) — not a generic error.

#### Scenario: Unconsented tenant at first sign-in

- **WHEN** a user signs in from a tenant where admin consent has not been granted
- **THEN** the guided admin-consent screen appears with the forwardable admin-consent URL, and no generic error is
  shown
