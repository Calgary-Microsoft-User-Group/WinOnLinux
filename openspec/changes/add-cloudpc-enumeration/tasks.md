# Tasks: add-cloudpc-enumeration

## 1. Graph client plumbing

- [x] 1.1 Implement the shared async Graph call wrapper: silent acquisition first (FR-4-AC-1), system proxy
      honored (§5.9), `429`/`Retry-After` with exponential backoff (§9), `Authorization` header redaction in logs
      (§10.7) — `src/winonlinux/graph_client.py`
- [x] 1.2 Distinguish proxy failures from plain network failures in the wrapper's error classification (§5.9, §9)
      — `GraphProxyError` vs `GraphNetworkError` in `graph_client.py`

## 2. CloudPC provider

- [x] 2.1 Implement `GET /me/cloudPCs` fetch following `@odata.nextLink` to exhaustion (FR-1-AC-1) —
      `src/winonlinux/cloudpc_provider.py`
- [x] 2.2 Parse entries to a typed model: display name, `status`, retained Graph `id` (FR-1-AC-2), provisioning
      metadata — `CloudPcEntry`
- [x] 2.3 Implement typed results `Enumerated | Empty | NoLicence | ConsentRequired | Failed`: `404` → NoLicence,
      `403` → ConsentRequired (G-42, see the dedicated requirement below), empty collection → Empty, transport/5xx →
      Failed with previous list retained (FR-1-AC-3, FR-1-AC-4)

## 3. Refresh lifecycle (§7.3)

- [x] 3.1 Trigger enumeration on sign-in, account switch, and manual refresh; run inside the active account's task
      group so switching cancels it (D-18, FR-3-AC-2) — `AuthManager.add_active_account_listener` (added to
      `auth_manager.py` as part of this change) fires synchronously on `start()`'s startup rebuild,
      `add_account()`'s first account, and every `switch_active_account()` call that actually changes the active
      account (after that method's own `cancel_group` call, so ordering is correct). `app.py`'s
      `_on_active_account_changed` handles all three by scheduling `_enumerate_active_account` under the new
      account's own task group via `task_registry.get_or_create_group(home_account_id).create_task(...)`, so a
      later switch cancels it through the same `cancel_group` mechanism (FR-3-AC-2) — covering sign-in and account
      switch end-to-end. **Manual refresh** still has no UI control to invoke it from — `refresh_after_action`
      (3.3) and `refresh_now` are ready for a future UI-shell change to call directly; that half remains a UI-shell
      dependency, not a defect in this change.
- [x] 3.2 Implement the 60 s foreground poll task, paused in background, resumed on foreground —
      `CloudPcProvider.start_polling`/`pause_polling`/`resume_polling`/`stop_polling`
- [x] 3.3 Expose an immediate-refresh hook for post-action status updates (D-8; consumed by the actions change) —
      `CloudPcProvider.refresh_after_action`

## 4. Phase 0 AVD bookmarks

- [x] 4.1 Define the bookmark store: JSON under `XDG_STATE_HOME` with `schemaVersion` (D-13), entries
      `{workspaceId, resourceId, displayName, kind}` — `src/winonlinux/avd_bookmarks.py`
- [x] 4.2 Provide bookmarks as AVD entries grouped by workspace, web launch only, native disabled with reason
      (FR-1-AC-5 Phase 0, FR-2-AC-1) — `group_by_workspace` + `NATIVE_DISABLED_REASON`; actual widget rendering is
      the UI shell change's job per design.md's Non-Goals ("Rendering — the UI shell change consumes this
      provider's output"), so this task is scoped to the data this change owns.
- [x] 4.3 Document the admin-provisioning steps (where the IDs come from: `Get-AzWvdWorkspace` etc., §5.3) —
      `docs/avd-bookmarks-admin-guide.md`

## 5. Fixtures and tests (§13.3, CI)

- [x] 5.1 Check in Graph fixtures: multi-page `/me/cloudPCs`, `404` no-licence, `403` consent-required (G-42), `429`
      with `Retry-After` — `tests/fixtures/graph/{cloudpcs_page1,cloudpcs_page2,cloudpcs_404,cloudpcs_403,
      cloudpcs_429}.json`
- [x] 5.2 Paging test: all pages surfaced, no partial result (FR-1-AC-1) — `tests/test_cloudpc_provider.py`
- [x] 5.3 State-distinction tests: Empty vs NoLicence vs Failed-keeps-list (FR-1-AC-3, FR-1-AC-4) —
      `tests/test_cloudpc_provider.py`
- [x] 5.3a Consent-required-specific test: HTTP `403` maps to a distinct `ConsentRequired` result, not conflated
      with `Failed` or `NoLicence` (G-42) — `test_403_returns_consent_required_carrying_admin_consent_url` and
      `test_four_result_types_are_distinct_via_isinstance` in `tests/test_cloudpc_provider.py`
- [x] 5.4 Throttling test: `Retry-After` honored, no user-visible error on first throttle (§9) —
      `tests/test_graph_client.py`, `tests/test_cloudpc_provider.py`
- [x] 5.5 Cancellation test: account switch cancels in-flight enumeration (FR-3-AC-2) —
      `tests/test_cloudpc_provider.py`
- [x] 5.6 Bookmark store round-trip and rendering test (FR-1-AC-5 Phase 0) — `tests/test_avd_bookmarks.py`
      (round-trip and `group_by_workspace`; see 4.2's note on rendering being the UI shell's scope)

## 6. Integration (live tenant, on-demand — needs E-1/E-3/E-5)

- [ ] 6.1 Verify enumeration against the pilot tenant with ≥ 1 provisioned Cloud PC, within the NFR-2 budget —
      NOT DONE: needs a real pilot tenant (E-1/E-3/E-5 prerequisites), not runnable in this environment.
- [ ] 6.2 Verify the no-licence `404` shape with an unlicensed account — NOT DONE: needs a real unlicensed test
      account against a real tenant; the fixture shape (5.1) is documented ASSUMED, not verified, per
      `tests/fixtures/graph/README.md`.
