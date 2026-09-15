# Tasks: add-cloudpc-enumeration

## 1. Graph client plumbing

- [ ] 1.1 Implement the shared async Graph call wrapper: silent acquisition first (FR-4-AC-1), system proxy
      honored (§5.9), `429`/`Retry-After` with exponential backoff (§9), `Authorization` header redaction in logs
      (§10.7)
- [ ] 1.2 Distinguish proxy failures from plain network failures in the wrapper's error classification (§5.9, §9)

## 2. CloudPC provider

- [ ] 2.1 Implement `GET /me/cloudPCs` fetch following `@odata.nextLink` to exhaustion (FR-1-AC-1)
- [ ] 2.2 Parse entries to a typed model: display name, `status`, retained Graph `id` (FR-1-AC-2), provisioning
      metadata
- [ ] 2.3 Implement typed results `Enumerated | Empty | NoLicence | Failed`: `404` → NoLicence, empty collection →
      Empty, transport/5xx → Failed with previous list retained (FR-1-AC-3, FR-1-AC-4)

## 3. Refresh lifecycle (§7.3)

- [ ] 3.1 Trigger enumeration on sign-in, account switch, and manual refresh; run inside the active account's task
      group so switching cancels it (D-18, FR-3-AC-2)
- [ ] 3.2 Implement the 60 s foreground poll task, paused in background, resumed on foreground
- [ ] 3.3 Expose an immediate-refresh hook for post-action status updates (D-8; consumed by the actions change)

## 4. Phase 0 AVD bookmarks

- [ ] 4.1 Define the bookmark store: JSON under `XDG_STATE_HOME` with `schemaVersion` (D-13), entries
      `{workspaceId, resourceId, displayName, kind}`
- [ ] 4.2 Render bookmarks as AVD entries grouped by workspace, web launch only, native disabled with reason
      (FR-1-AC-5 Phase 0, FR-2-AC-1)
- [ ] 4.3 Document the admin-provisioning steps (where the IDs come from: `Get-AzWvdWorkspace` etc., §5.3)

## 5. Fixtures and tests (§13.3, CI)

- [ ] 5.1 Check in Graph fixtures: multi-page `/me/cloudPCs`, `404` no-licence, `429` with `Retry-After`
- [ ] 5.2 Paging test: all pages surfaced, no partial result (FR-1-AC-1)
- [ ] 5.3 State-distinction tests: Empty vs NoLicence vs Failed-keeps-list (FR-1-AC-3, FR-1-AC-4)
- [ ] 5.4 Throttling test: `Retry-After` honored, no user-visible error on first throttle (§9)
- [ ] 5.5 Cancellation test: account switch cancels in-flight enumeration (FR-3-AC-2)
- [ ] 5.6 Bookmark store round-trip and rendering test (FR-1-AC-5 Phase 0)

## 6. Integration (live tenant, on-demand — needs E-1/E-3/E-5)

- [ ] 6.1 Verify enumeration against the pilot tenant with ≥ 1 provisioned Cloud PC, within the NFR-2 budget
- [ ] 6.2 Verify the no-licence `404` shape with an unlicensed account
