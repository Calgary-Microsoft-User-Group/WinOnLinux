# Tasks: add-avd-feed-provider

## 1. Gate preconditions (blocking — no implementation below this line until all are checked)

- [ ] 1.1 Verify the Stage 0 finding document exists and records outcome 1 or 2: the working identity (own
      registration or first-party AVD client ID), the granted scope, and the consent path (§11.1). If outcome 3 or
      the D-14 timebox expired without an HTTP 200, stop: this change is not implemented and the product ships
      web-only.
- [ ] 1.2 Verify Gate LG-1's written legal position covers the identity being used and the capture method that
      produced the schema; record the reference in this change.
- [ ] 1.3 Verify the Stage 1 outputs are checked in: feed schema document (both legs and their chaining), the
      field-mapping table, and redacted discovery + workspace-feed fixtures (§13.3).
- [ ] 1.4 Confirm the volatile facts this change relies on were re-verified at Stage 0 per risk 14 (discovery URL,
      feed audience, client-ID values).

## 2. Provider skeleton and identity wiring

- [ ] 2.1 Define the AVD resource model (workspace, desktop, RemoteApp) with the identifiers §5.3 web launch and
      §5.2 config acquisition require.
- [ ] 2.2 Add the feed-audience token request path through the Auth/Account Manager, with the client identity taken
      from a single configuration point (Stage 0 outcome), and silent acquisition before every call (FR-4-AC-1).
- [ ] 2.3 Route all feed HTTP through the shared proxy-honoring HTTP stack (§5.9).

## 3. Feed protocol implementation (against the captured schema only)

- [ ] 3.1 Implement feed discovery: request, response parse, extraction of workspace-feed URL(s).
- [ ] 3.2 Implement workspace feed download and the discovery→download chaining exactly as the Stage 1 schema
      documents it.
- [ ] 3.3 Implement the pure-function feed parser: bytes → resource model; unknown fields ignored; no I/O.
- [ ] 3.4 Expose routing-field access for the Connection-Config Provider without leaking feed internals to other
      consumers (§5.1).

## 4. Error taxonomy and isolation

- [ ] 4.1 Map discovery/download failures to the §9 inline AVD-section error with retry; assert Cloud PC section
      isolation.
- [ ] 4.2 Implement feed-401-after-issuance as identity-model failure: `Unavailable(reason)`, audience logged, no
      ReauthRequired transition.
- [ ] 4.3 Report proxy failures distinguishably from offline (§5.9).

## 5. Tests

- [ ] 5.1 Unit: parser against the checked-in discovery and workspace-feed fixtures, offline (§13.4).
- [ ] 5.2 Unit: unknown-field tolerance; malformed/truncated response → parse error mapped to the §9 feed error.
- [ ] 5.3 Unit: silent-acquisition-precedes-every-call assertion for feed calls (FR-4-AC-1 instrumented test).
- [ ] 5.4 Integration (on-demand, live tenant E-2): enumerate a workspace with a desktop and a RemoteApp; verify
      FR-1-AC-5 grouping within the NFR-2 budget.

## 6. UI integration and retirement of the Phase 0 bookmark provider

- [ ] 6.1 Wire the provider into the resource layer and the AVD section of the resource list (grouped by
      workspace).
- [ ] 6.2 Retire the admin-provisioned static AVD bookmark provider; keep web launch working for feed-enumerated
      entries.
- [ ] 6.3 Update user documentation: AVD enumeration no longer needs admin-provisioned IDs.
