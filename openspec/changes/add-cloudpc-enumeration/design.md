# Design: add-cloudpc-enumeration

## Context

The Graph CloudPC provider is the least risky component in the system — a documented v1.0 API behind delegated
consent — which is why Phase 0 leads with it. Its design constraints come from elsewhere in the spec: every call is
preceded by silent acquisition (FR-4-AC-1), all work runs inside the active account's task group so switching
cancels it (D-18, FR-3-AC-2), and the provider must be exercisable purely from fixtures with no network (§13.4).

## Goals / Non-Goals

**Goals:**

- Complete, paged, active-account-scoped Cloud PC enumeration with the FR-1 state distinctions.
- §7.3 status refresh lifecycle bound to window foreground state.
- Fixture-first parser/normalizer so paging, `404`, and `429` shapes run in CI (§13.3, §13.4).
- Phase 0 AVD bookmarks: a static, admin-supplied list rendered alongside, web-launch-only.

**Non-Goals:**

- AVD feed enumeration (FR-1-AC-5 complete) — `add-avd-feed-provider`, gated on Stage 0/1.
- Management actions and launch-detail retrieval — `add-cloudpc-actions` / `add-web-launcher` own those endpoints.
- Rendering — the UI shell change consumes this provider's output; NFR-2 measurement lands there.

## Decisions

- **Async HTTP client with system-proxy support.** The client honors `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` and
  desktop proxy settings for Graph traffic (§5.9). What the Flatpak sandbox exposes is V2's problem; this provider
  just uses the environment it is given.
- **Provider returns typed results, never raises for expected shapes.** `Enumerated(list)`, `Empty`, `NoLicence`,
  `Failed(error, keep_previous=True)` — the FR-1-AC-3/AC-4 distinctions are values, so the UI cannot conflate them.
  `404` → `NoLicence`; empty collection → `Empty`; transport/5xx → `Failed` and the cached list stays.
- **Paging followed unconditionally.** The fetch loop follows `@odata.nextLink` to exhaustion before returning
  (FR-1-AC-1); partial pages are never surfaced as a complete result.
- **Polling owned by the provider, gated by the shell.** A per-account polling task (60 s) started/stopped on
  foreground/background signals from the shell (§7.3); action completion observation reuses this same refresh
  (D-8 — no separate operation-polling machinery).
- **`429` honored centrally.** `Retry-After` respected, exponential backoff, no user-visible error unless
  persistent (§9); implemented in the shared Graph call wrapper so FR-5 actions inherit it.
- **Bookmarks are configuration, not discovery.** A JSON store under `XDG_STATE_HOME` (D-13, `schemaVersion`)
  holding admin-supplied `{workspaceId, resourceId, displayName, kind}`; rendered grouped by workspace; web launch
  only, native disabled with the FR-2-AC-1 reason string ("workspace/resource ID unknown" does not apply — the
  native reason in Phase 0 is that connection configuration cannot be acquired).

## Risks / Trade-offs

- [Graph latency or large tenants blow the NFR-2 budget] → Budget is p95 for ≤ 25 resources; paging is concurrent
  with rendering only if measurement demands it — start simple, measure at V3 alongside the UI shell.
- [`/me/cloudPCs` shape drift] → v1.0 is a versioned contract; fixtures pin the parsed shape and CI catches
  regressions in our parsing, while a true server-side contract break surfaces as `Failed` with the list retained
  (FR-1-AC-4), not a crash.
- [Bookmark misconfiguration (bad IDs)] → IDs are opaque to the client; a wrong bookmark fails at web launch in the
  browser, which is Microsoft's error surface. Documented in the bookmark store's admin-facing notes.
- [Polling wakes the app in background] → Poll task pauses off-foreground (§7.3), which is also the NFR-4 friendly
  behavior.

## Open Questions

- None blocking. The status-value × action enablement matrix (G-19, spec §7.4 "Outstanding (P1)") is consumed by
  the UI shell and actions changes; enumeration only carries the raw `status` through.
