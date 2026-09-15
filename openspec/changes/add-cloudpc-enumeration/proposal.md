# Proposal: add-cloudpc-enumeration

## Why

FR-1 makes the resource list the main area of the application, and for Windows 365 the entire enumeration surface
is public and supported: Graph v1.0 `GET /me/cloudPCs` with delegated `CloudPC.Read.All`. This is the first
provider built on the Auth/Account Manager and the data source for the UI shell, the web launcher, and every FR-5
action (all of which reuse the retained Graph `id`, FR-1-AC-2).

## What Changes

- Implement the **Graph CloudPC provider** (§5.1): enumeration via Graph v1.0 `GET /me/cloudPCs`, following
  **every page** of a paged response (FR-1-AC-1), within the NFR-2 latency budget.
- Retain the Graph `id` per entry as the sole downstream identifier — web launch and FR-5 actions perform **no
  second lookup** (FR-1-AC-2).
- Distinguish the three non-error shapes: populated list, **empty state** (licensed, nothing provisioned), and the
  **distinct no-licence empty state** on Graph `404` (FR-1-AC-3). Neither empty shape renders as an error.
- A failed refresh keeps the previously enumerated list visible with an error indicator — it never clears the list
  (FR-1-AC-4).
- Implement **status refresh** per §7.3: poll while foregrounded (60 s, paused in background), refresh immediately
  after any invoked action and on manual refresh; honor `429` + `Retry-After` with exponential backoff (§9).
- Scope all enumeration to the **active account**, with silent token acquisition before every call (FR-4-AC-1) and
  cancellation of in-flight enumeration on account switch (FR-3-AC-2).
- Honor the environment's proxy configuration for all Graph traffic (§5.9).
- Implement the **Phase 0 AVD bookmark mechanism** (FR-1-AC-5 partial): admin-provisioned workspace/resource IDs,
  stored per D-13, rendered as AVD entries grouped by workspace with web launch only — native shown disabled with
  reason (FR-2-AC-1). Self-enumeration from the AVD feed is a separate change (`add-avd-feed-provider`).

## Capabilities

### New Capabilities

- `cloudpc-enumeration`: Windows 365 Cloud PC enumeration and status refresh via Graph v1.0, plus the Phase 0
  admin-provisioned AVD bookmark list.

### Modified Capabilities

_None — this is the first specification of this capability._

## Impact

- New Resource layer component: Graph CloudPC provider (§5.1); consumed by the UI shell, web launcher, and
  management actions changes.
- Depends on `add-auth-account-manager` (silent acquisition, active-account scoping, cancellation) and
  `add-app-foundation` (asyncio task groups D-18, XDG state store D-13 for bookmarks, logging redaction §10.7).
- Test fixtures per §13.3: paged `/me/cloudPCs`, `404` no-licence, `429` with `Retry-After` — the only CI-runnable
  coverage for this provider (§13.4).
- Verified surface: Graph v1.0 list API is documented and stable. Assumed until measured: the NFR-2 budget against
  PyGObject rendering (V3 can fire D-16's revisit trigger, but measurement happens in the UI shell change).
