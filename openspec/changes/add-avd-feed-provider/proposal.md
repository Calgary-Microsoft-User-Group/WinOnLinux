# Proposal: add-avd-feed-provider

> **Gate status — read first.** This change is blocked on three gates and must not start implementation until all
> three have recorded outcomes: **Stage 0** (app-identity feasibility spike, 3-working-day hard timebox, D-14),
> **Gate LG-1** (written legal position on first-party client-ID reuse and traffic capture), and **Stage 1** (feed
> schema capture: both legs, field-mapping table, checked-in fixtures). A negative outcome at any of them means the
> product ships **web-only per D-14** — that is a pre-committed decision, not a problem this change engineers around.

## Why

FR-1 requires the client to enumerate AVD desktops and RemoteApps for the signed-in user, and Graph cannot do it:
Graph is control-plane only, and AVD objects live under ARM behind Azure RBAC that ordinary end users lack (§3,
spec.md fact list). The AVD workspace feed — feed discovery followed by workspace feed download — is the only route
to AVD enumeration (FR-1-AC-5) and is also the sole source of the routing fields the native path composes into a
`.rdpw` (§8). Until this provider exists, AVD support is limited to admin-provisioned bookmarks (Phase 0), and the
native path has no data to build connections from.

## What Changes

- New **AVD Feed provider** in the resource layer (§5.1): feed discovery against
  `https://rdweb.wvd.microsoft.com/api/arm/feeddiscovery` (the only published endpoint), then workspace feed
  download from the second-leg URL returned by the discovery response. Confirming that chaining is a Stage 1
  deliverable this change consumes, not reproduces.
- Feed token acquisition through the Auth/Account Manager with audience `https://www.wvd.microsoft.com/.default`,
  using whichever identity Stage 0 selected: the project's own registration, or the documented fallback of the
  first-party AVD client ID (§6.2, §11.1).
- AVD **enumeration** completing FR-1-AC-5: workspaces, desktops and RemoteApps assigned to the account, grouped by
  workspace, with no admin pre-provisioning. Replaces the Phase 0 static bookmark provider (§5.1).
- Feed **error handling** per §9: discovery/download failure renders an inline error with retry in the AVD section
  only — the Cloud PC section is unaffected; a feed `401` despite successful token issuance is treated as an
  identity-model failure (native `Unavailable(reason)`, audience logged), never as ordinary expiry.
- **Proxy behavior** per §5.9: all feed traffic honors the system/session proxy configuration, with proxy failures
  reported distinguishably from plain network failure.
- **Checked-in, redacted feed fixtures** (§13.3) so the parser and grouping logic run in CI with no network (§13.4).
- Explicitly **not** in scope: composing the `.rdpw` (that is `add-connection-config-provider`), and anything FreeRDP
  already implements — Entra auth, ARM gateway negotiation, reverse connect, RDP/RDSTLS (§2.2).

## Capabilities

### New Capabilities

- `avd-feed`: feed discovery, workspace feed download, AVD resource enumeration, feed error taxonomy, and the
  fixture contract that makes the feed parser CI-testable offline.

### Modified Capabilities

<!-- none — no existing deployed capability changes -->

## Impact

- **Code**: new provider module in the resource layer; the Phase 0 admin-provisioned AVD bookmark list is retired
  when this lands (§11, FR-1-AC-5 "Phase 1 complete").
- **Dependencies**: Auth/Account Manager (silent acquisition before every feed call, FR-4-AC-1); Stage 1 outputs —
  schema document, field-mapping table, and captured fixtures — are hard inputs.
- **Consumers**: the UI resource list (AVD section, grouped by workspace) and `add-connection-config-provider`,
  which reads the workspace feed's routing fields through this provider.
- **Risk posture**: this change sits directly on risks 4 and 4a (§12) — the undocumented protocol and the unproven
  capture method. The schema this provider implements against is **assumed until Stage 1 verifies it**.
