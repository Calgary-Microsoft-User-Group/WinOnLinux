## Why

FR-5 commits the client to Cloud PC management controls — restart, rename, troubleshoot, reprovision — that
Microsoft only exposes for end users through Graph **beta** `/me` endpoints (§7.1), a surface marked "production
use is not supported" (§7.2). The product still ships them in Phase 0 (§11.1), so the work is as much about
containing that beta dependency — feature flag, graceful degradation, no crash on contract change — as about
issuing the calls.

## What Changes

- New **Management Action service** (§5.1): Restart (`reboot`), Rename, Troubleshoot via
  `POST /me/cloudPCs/{id}/...` (Graph beta), each behind a beta-API feature flag that isolates version
  selection (§7.2), with progress display and outcome surfacing (FR-5-AC-1).
- **Reprovision** as a destructive action: explicit typed or checked confirmation, cancel issues no call, the
  call is never batched and never automatically retried (FR-5-AC-2, §10.6).
- **Restore/Resize visibility**: shown only for admin-capable accounts, detected from token role/`wids` claims;
  capability-unknown is treated as not-capable (D-7, FR-5-AC-3). No admin-path invocation ships in this change —
  visibility gating only, since the admin `virtualEndpoint` paths are a later concern.
- **State-gated enablement** per §7.4: an action whose precondition the current status does not satisfy is
  disabled with a reason; unknown or unrecognized status disables; a triggered transition suppresses
  conflicting actions until the next refresh. Interim rule until the full status×action matrix lands (open
  item G-19): anything not affirmatively known to permit an action disables it.
- **Completion by polling**: outcomes observed via the existing §7.3 refresh with a stated timeout — no
  operation-polling machinery (D-8); status chip reflects the transition on the next refresh (FR-5-AC-1).
- **Failure taxonomy** per §9: consent-missing `403` → guided admin-consent flow (FR-5-AC-4); beta contract
  error → only the affected action disabled with "Action unavailable — Microsoft API change", logged for
  triage (FR-5-AC-5); national clouds without Cloud PC Graph APIs → FR-5 disabled wholesale with an
  explanatory message (FR-5-AC-6); `429` honors `Retry-After`.
- **Fixtures** per §13.3: mutated beta action shapes, `403` unconsented, `429` with `Retry-After`.

## Capabilities

### New Capabilities

- `cloudpc-actions`: Cloud PC management actions over Graph beta `/me` endpoints — invocation, destructive
  confirmation, admin-capability gating, state gating, and beta-contract containment (FR-5).

### Modified Capabilities

<!-- none — no existing capability specs yet -->

## Impact

- New module: Management Action service. Response-shape handling is CI-testable from fixtures (§13.2, §13.3).
- Depends on: `add-auth-account-manager` (silent acquisition before every call, FR-4-AC-1; incremental consent
  for `CloudPC.ReadWrite.All`, §6.2) and `add-cloudpc-enumeration` (resource ids, §7.3 refresh loop that
  observes outcomes).
- Consumed by: `add-ui-shell` (actions menu §4.3, confirmation dialog, toasts, status chip).
- Scope note: `CloudPC.ReadWrite.All` requires tenant admin consent (§6.2); the guided-consent flow this change
  triggers is specified against §9 and exercised by FR-5-AC-4.
