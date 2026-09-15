# Tasks: add-auth-account-manager

## 1. MSAL integration and interactive sign-in

- [ ] 1.1 Add `msal` and `msal-extensions` dependencies; create the AuthManager module owning one
      `PublicClientApplication` configured with the D-19 client ID and `organizations` authority
- [ ] 1.2 Implement the loopback listener: asyncio server on `127.0.0.1:0` (D-6), `state` validation,
      single-response acceptance, configurable timeout, clean shutdown (§10.3)
- [ ] 1.3 Implement interactive sign-in via `initiate_auth_code_flow` / `acquire_token_by_auth_code_flow` with the
      system browser, requesting `openid profile offline_access CloudPC.Read.All` (§6.2)
- [ ] 1.4 Handle user cancellation: return to prior state, no error dialog (§9)

## 2. Token cache and keyring

- [ ] 2.1 Wire `msal-extensions` keyring-backed persistence (libsecret/KWallet); detect Secret Service availability
      at startup
- [ ] 2.2 Implement the D-2 refusal state: no keyring → plain explanation, sign-in disabled, no fallback
- [ ] 2.3 Enumerate cached accounts at startup and rebuild the account list keyed by home account ID (§6.3)
- [ ] 2.4 Serialize cache writes per account through a single writer; persist rotated refresh tokens immediately
      (FR-4-AC-2)

## 3. Auth state machine (§6.4)

- [ ] 3.1 Implement per-account `AuthState` (SignedOut, InteractiveAuth, Active, SilentRefresh, ReauthRequired,
      Offline, DeviceCABlocked) with an explicit transition table
- [ ] 3.2 Implement MSAL error classification as a pure function: `invalid_grant`, `interaction_required`+`claims`,
      network errors, `AADSTS53000` family (§6.5)
- [ ] 3.3 Implement silent acquisition with clock-skew safety margin, retry/backoff on network failure, Offline
      fallback while an unexpired AT exists (FR-4-AC-5)
- [ ] 3.4 Implement claims-challenge passthrough: verbatim `claims` into interactive re-auth, bounded retry counter
      (FR-4-AC-4)
- [ ] 3.5 Implement ReauthRequired banner state with `login_hint` prefill; isolate to the one account (FR-4-AC-3,
      FR-3-AC-5)

## 4. Concurrency (§6.3)

- [ ] 4.1 Implement per-account single-flight: asyncio lock + shared in-flight future so concurrent acquisitions
      await one refresh (FR-4-AC-6)
- [ ] 4.2 Expose `acquire_token_silently(account, scopes)` as the single entry point used before every outbound
      call (FR-4-AC-1); no timer-based refresh anywhere

## 5. Multi-account operations (FR-3)

- [ ] 5.1 Implement add-account without disturbing existing accounts (FR-3-AC-1)
- [ ] 5.2 Implement active-account switching: swap context, cancel the previous account's task group (D-18,
      FR-3-AC-2)
- [ ] 5.3 Implement per-account sign-out: remove cache entries and UI resources; other accounts unaffected
      (FR-3-AC-3)

## 6. Consent flows

- [ ] 6.1 Implement incremental consent for `CloudPC.ReadWrite.All` on first management action (§6.2)
- [ ] 6.2 Implement the guided admin-consent surface: detection of consent-missing errors, forwardable
      admin-consent URL (§9)

## 7. Tests (§13.2 — unit/fixture level, CI)

- [ ] 7.1 State machine tests with synthesized MSAL responses covering every §6.4 transition
- [ ] 7.2 N-simultaneous-acquisitions test asserting one network refresh and one surviving refresh token
      (FR-4-AC-6)
- [ ] 7.3 Claims-challenge test asserting verbatim passthrough, no unmodified retry, bounded retries (FR-4-AC-4)
- [ ] 7.4 Network-fault-injection test asserting Offline (not ReauthRequired) with cached AT reuse (FR-4-AC-5)
- [ ] 7.5 Device-state error tests mapping the `AADSTS53000` family to DeviceCABlocked with no retry (§6.5)
- [ ] 7.6 Instrumented test asserting silent acquisition precedes every outbound call (FR-4-AC-1)
- [ ] 7.7 Keyring-absent test asserting the D-2 refusal state

## 8. Verification hooks (not closable inside this change)

- [ ] 8.1 Record V1 (`msal-extensions` Linux locking/persistence) result when Gate STACK runs; if it fails, note
      that D-17's revisit trigger fires and the app-level layer of 4.1/2.4 is the specified fallback
- [ ] 8.2 Record the Stage 0 confirmation of the exact `AADSTS` device-state code set and the D-6 ephemeral-port
      behavior
