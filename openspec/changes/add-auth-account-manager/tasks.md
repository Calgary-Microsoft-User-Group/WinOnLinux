# Tasks: add-auth-account-manager

## 1. MSAL integration and interactive sign-in

- [x] 1.1 Add `msal` and `msal-extensions` dependencies; create the AuthManager module owning one
      `PublicClientApplication` configured with the D-19 client ID and `organizations` authority
      (`src/winonlinux/auth_manager.py`, `pyproject.toml`). D-19's real client ID does not exist yet
      (Stage 0 work) — `AuthManager` accepts a `client_id` constructor argument and falls back to a
      loud, WARNING-logged placeholder GUID (`_CLIENT_ID_PLACEHOLDER`) until it does; this is
      expected and documented, not a gap in this task.
- [x] 1.2 Implement the loopback listener: asyncio server on `127.0.0.1:0` (D-6), `state` validation,
      single-response acceptance, configurable timeout, clean shutdown (§10.3)
      (`src/winonlinux/auth_loopback.py`, `tests/test_auth_loopback.py`)
- [x] 1.3 Implement interactive sign-in via `initiate_auth_code_flow` / `acquire_token_by_auth_code_flow` with the
      system browser, requesting `openid profile offline_access CloudPC.Read.All` (§6.2)
      (`AuthManager._run_interactive_auth_code_flow`, `AuthManager.add_account`)
- [x] 1.4 Handle user cancellation: return to prior state, no error dialog (§9)
      (`SignInCancelled`, tested in `test_auth_manager.py`)

## 2. Token cache and keyring

- [x] 2.1 Wire `msal-extensions` keyring-backed persistence (libsecret/KWallet); detect Secret Service availability
      at startup (`src/winonlinux/auth_cache.py`'s `build_persisted_cache`/`detect_keyring_available`, called from
      `AuthManager.start`)
- [ ] 2.2 Implement the D-2 refusal state: no keyring → plain explanation, sign-in disabled, no fallback
      NOT fully closable from this change: the *mechanism* is done — `auth_cache.build_persisted_cache` always
      raises `KeyringUnavailable` with no plaintext/file fallback (`fallback_to_plaintext` is hard-coded `False`),
      `AuthManager.start()` propagates it unmodified, and `app.py`'s `_start_auth_manager` catches it and stashes
      it on `self.auth_manager_start_error` rather than disabling sign-in itself. The actual "plain explanation"
      UI surface has no consumer yet because no UI-shell change is applied (`add-ui-shell` is still an unapplied
      OpenSpec change) — presenting that explanation and disabling the sign-in affordance is explicitly out of
      scope for this change per `auth_manager.py`'s own docstring ("a UI-level concern outside this change's
      scope"). Re-check this box once add-ui-shell consumes `auth_manager_start_error`.
- [x] 2.3 Enumerate cached accounts at startup and rebuild the account list keyed by home account ID (§6.3)
      (`AuthManager.start`)
- [x] 2.4 Serialize cache writes per account through a single writer; persist rotated refresh tokens immediately
      (FR-4-AC-2) (`auth_cache.CacheWriteSerializer`/`cache_write_serializer`, wrapped around every
      cache-mutating MSAL call in `AuthManager`; msal-extensions' `PersistedTokenCache` persists on every mutation,
      so no separate "persist now" step was needed). Note: the serializer is process-wide (across *all* accounts'
      writes), not one lock per account — this is the shared-contract's documented reason (a `PersistedTokenCache`
      persists the whole blob on any change, so per-account locks alone would not prevent two different accounts'
      writes from racing on the shared persist step); it is distinct from, and layered under, `AuthManager`'s own
      per-account single-flight lock (4.1).

## 3. Auth state machine (§6.4)

- [x] 3.1 Implement per-account `AuthState` (SignedOut, InteractiveAuth, Active, SilentRefresh, ReauthRequired,
      Offline, DeviceCABlocked) with an explicit transition table
      (`src/winonlinux/auth_state.py`, `tests/test_auth_state.py`)
- [x] 3.2 Implement MSAL error classification as a pure function: `invalid_grant`, `interaction_required`+`claims`,
      network errors, `AADSTS53000` family (§6.5) (`src/winonlinux/auth_errors.py`,
      `tests/test_auth_errors.py`) — NETWORK_ERROR is deliberately never produced by this pure function per the
      shared contract; callers construct it directly around a caught network exception (see 3.3/7.4).
- [ ] 3.3 Implement silent acquisition with clock-skew safety margin, retry/backoff on network failure, Offline
      fallback while an unexpired AT exists (FR-4-AC-5)
      PARTIALLY done: `AuthManager._acquire_token_silently_uncached` calls
      `acquire_token_silent_with_error` and, on a network exception, moves the account straight to `OFFLINE`
      and raises `OfflineNoCachedTokenError` (relying on the documented, unverified assumption that MSAL's own
      cache lookup already returns a still-valid cached AT with zero network call whenever one exists — see the
      method's own code comment). What is genuinely missing, not merely assumed: there is no retry/backoff loop
      around a transient network failure (the first exception raises immediately), and no explicit clock-skew
      safety-margin calculation anywhere in this module (design.md's "default 5 min" decision is not implemented;
      whatever margin exists is whatever MSAL's own cache-validity check applies internally, unverified here).
      Left unchecked rather than checked-with-caveats because both of those are literal, unambiguous parts of this
      task's own wording.
- [x] 3.4 Implement claims-challenge passthrough: verbatim `claims` into interactive re-auth, bounded retry counter
      (FR-4-AC-4) (`auth_state.AccountAuthState.apply_error`'s CLAIMS_CHALLENGE branch, `MAX_CLAIMS_CHALLENGE_RETRIES
      = 3`; `AuthManager.reauth_from_banner` forwards `pending_claims` verbatim as `claims_challenge`)
- [x] 3.5 Implement ReauthRequired banner state with `login_hint` prefill; isolate to the one account (FR-4-AC-3,
      FR-3-AC-5) (`AccountAuthState` is one object per `home_account_id`, so a transition on one account can never
      touch another's; `AuthManager.reauth_from_banner` reads `account_state.login_hint` as the prefill). No actual
      banner *widget* exists yet — that is UI-shell's job — this task's own scope is the backend state/entry point
      a banner would call, which is what is implemented and tested.

## 4. Concurrency (§6.3)

- [x] 4.1 Implement per-account single-flight: asyncio lock + shared in-flight future so concurrent acquisitions
      await one refresh (FR-4-AC-6) (`AuthManager.acquire_token_silently`, tested in
      `test_concurrent_acquire_token_silently_calls_collapse_into_one_underlying_call`)
- [x] 4.2 Expose `acquire_token_silently(account, scopes)` as the single entry point used before every outbound
      call (FR-4-AC-1); no timer-based refresh anywhere (`AuthManager.acquire_token_silently`; no timer/scheduled
      task exists anywhere in this module)

## 5. Multi-account operations (FR-3)

- [x] 5.1 Implement add-account without disturbing existing accounts (FR-3-AC-1) (`AuthManager.add_account`,
      tested in `test_add_account_does_not_disturb_existing_account_or_active_id`)
- [x] 5.2 Implement active-account switching: swap context, cancel the previous account's task group (D-18,
      FR-3-AC-2) (`AuthManager.switch_active_account`)
- [x] 5.3 Implement per-account sign-out: remove cache entries and UI resources; other accounts unaffected
      (FR-3-AC-3) (`AuthManager.sign_out` removes the MSAL cache entry and local `AccountAuthState`/single-flight
      bookkeeping for exactly the target account). "UI resources" has nothing to remove yet — no UI-shell change is
      applied — so that half of the wording is vacuously satisfied rather than actually exercised.

## 6. Consent flows

- [ ] 6.1 Implement incremental consent for `CloudPC.ReadWrite.All` on first management action (§6.2)
      NOT implemented: `AuthManager` has no call site that requests `CloudPC.ReadWrite.All` incrementally on a
      management action, because no management-action consumer exists yet (`add-cloudpc-actions` is still an
      unapplied OpenSpec change) — there is nothing in this codebase to trigger an incremental-consent request
      from. `acquire_token_silently(account, scopes)` accepts an arbitrary `scopes` list per call, which is the
      mechanism a future caller would use, but that is not the same as this task's own ask being done.
- [x] 6.2 Implement the guided admin-consent surface: detection of consent-missing errors, forwardable
      admin-consent URL (§9) (`auth_errors.classify_msal_error`'s CONSENT_REQUIRED detection,
      `auth_errors.compose_admin_consent_url`, surfaced via `ConsentRequiredError.admin_consent_url`). The actual
      UI "surface" that forwards the URL to the user has no consumer yet (no UI-shell); detection and URL
      composition — this task's concrete, testable half — are done and tested.

## 7. Tests (§13.2 — unit/fixture level, CI)

- [x] 7.1 State machine tests with synthesized MSAL responses covering every §6.4 transition
      (`tests/test_auth_state.py` — one test per transition-table row plus DEVICE_CA_BLOCKED terminality,
      bounded-retry, and verbatim-claims edge cases)
- [x] 7.2 N-simultaneous-acquisitions test asserting one network refresh and one surviving refresh token
      (FR-4-AC-6) (`test_concurrent_acquire_token_silently_calls_collapse_into_one_underlying_call`)
- [x] 7.3 Claims-challenge test asserting verbatim passthrough, no unmodified retry, bounded retries (FR-4-AC-4)
      (`test_claims_pending_carries_exact_string_verbatim`,
      `test_claims_challenge_bounded_retry_lands_in_reauth_required_on_fourth`,
      `test_acquire_token_silently_raises_claims_challenge_and_drives_state`)
- [ ] 7.4 Network-fault-injection test asserting Offline (not ReauthRequired) with cached AT reuse (FR-4-AC-5)
      PARTIALLY done: `test_acquire_token_silently_raises_offline_no_cached_token_on_network_exception` proves a
      network exception drives the account to `OFFLINE` rather than `REAUTH_REQUIRED`. What is not tested: "cached
      AT reuse" — no test exercises a scenario where a still-valid cached access token is actually returned while
      offline, because that behavior (per 3.3's note) is assumed to live entirely inside MSAL's own silent-call
      caching, which is monkeypatched out in every test here rather than exercised. Left unchecked because the
      literal "with cached AT reuse" clause is unverified, not because the Offline-vs-ReauthRequired half is wrong.
- [x] 7.5 Device-state error tests mapping the `AADSTS53000` family to DeviceCABlocked with no retry (§6.5)
      (`test_auth_errors.py`'s `test_device_ca_blocked_*` tests, `test_auth_state.py`'s
      `test_device_ca_blocked_is_terminal_only_sign_out_moves_away`,
      `test_auth_manager.py`'s `test_acquire_token_silently_raises_device_ca_blocked_and_drives_state`)
- [ ] 7.6 Instrumented test asserting silent acquisition precedes every outbound call (FR-4-AC-1)
      NOT closable from this change: there is no outbound Graph/feed/launch call site anywhere in this codebase
      yet to instrument against (`add-avd-feed-provider`, `add-cloudpc-enumeration`, `add-cloudpc-actions` are all
      still unapplied OpenSpec changes) — `acquire_token_silently` exists and is tested as *a* single entry point,
      but a test proving every *other* outbound call goes through it first needs those other call sites to exist.
- [x] 7.7 Keyring-absent test asserting the D-2 refusal state
      (`tests/test_auth_cache.py`'s `test_build_persisted_cache_raises_keyring_unavailable_when_build_fails` and
      `test_build_persisted_cache_raises_when_msal_extensions_missing` prove the no-fallback exception path this
      change owns; the UI-level "refusal state" presentation itself is out of scope here — see 2.2's note)

## 8. Verification hooks (not closable inside this change)

- [ ] 8.1 Record V1 (`msal-extensions` Linux locking/persistence) result when Gate STACK runs; if it fails, note
      that D-17's revisit trigger fires and the app-level layer of 4.1/2.4 is the specified fallback
      Needs a real Linux host with `msal-extensions` and an actual Secret Service provider to run Gate STACK
      against — not runnable from this Windows, no-Python environment. Matches how add-app-foundation's task 6.2
      was left open for the same reason.
- [ ] 8.2 Record the Stage 0 confirmation of the exact `AADSTS` device-state code set and the D-6 ephemeral-port
      behavior
      Needs a real Entra tenant to trigger genuine device-state Conditional Access errors and observe the actual
      `error_codes`/`AADSTS` text, plus real Entra loopback-redirect matching behavior for an ephemeral port —
      neither is available from this environment. `auth_errors.py`'s `_DEVICE_CA_BLOCKED_CODES`/`_SUBSTRINGS` are
      explicitly marked ASSUMED in code pending this confirmation.
