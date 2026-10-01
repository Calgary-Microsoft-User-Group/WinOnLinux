## Context

§7.1's endpoint matrix is the ground truth: self-service actions exist only on Graph **beta** `/me` paths, while
Restore/Resize/EndGracePeriod have no `/me` path at all. §7.2 records Microsoft's own warning that beta is
unsupported for production, and §12 risk 2 carries the exposure. The decision register already settles the two
design-shaping questions: completion is observed by polling the existing refresh (D-8), and admin capability
comes from token role/`wids` claims rather than a probe call (D-7).

Verified vs unverified, per the repo's conventions: the endpoint matrix and permissions are *documented*
(§7.1, July 2026); that the beta `/me` actions behave as documented against a live tenant is *unverified until
Phase 0 integration testing* (§11.2 I-level); the `wids`-claim heuristic is *decided but accepts false
negatives* (D-7 — Intune-RBAC-only admins see Restore/Resize hidden, the direction FR-5-AC-3 mandates).

## Goals / Non-Goals

**Goals:**

- FR-5-AC-1…AC-6 implementable and testable, with every beta dependency behind one feature flag.
- A beta contract change degrades exactly one action, never enumeration or either connect method (FR-5-AC-5).
- Destructive-action handling that cannot fire accidentally: confirmation, no batching, no auto-retry.
- State gating that never offers an action that then fails on state or authorization (§7.4, FR-5-AC-3).

**Non-Goals:**

- Admin-path (`/deviceManagement/virtualEndpoint`) invocation of Restore/Resize — this change gates
  *visibility* only; invoking them is future work sized after real admin-account demand.
- The full status×action enablement matrix, tooltip strings, and deallocated-start timeout — open item G-19
  in `gapsandrecommendations.md`; this change ships the §7.4 interim rule (unknown → disabled).
- Operation-status polling machinery — rejected by D-8.
- UI widgets (menus, dialogs, toasts) — `add-ui-shell`; this change exposes the service interface and states
  the UI binds to.

## Decisions

- **One `invokeAction(account, cloudPcId, action) -> ActionOutcome` service interface.** Every invocation:
  silent token acquisition first (FR-4-AC-1) → capability/state precondition check → POST → outcome. Outcomes
  are `Accepted`, `ConsentRequired`, `ContractError`, `Throttled`, `Failed(reason)` — the §9 rows as a closed
  enum, so the UI cannot invent handling.
- **Version selection isolated behind the beta flag** (§7.2): endpoint construction lives in one place keyed by
  flag state; when the `/me` paths promote to v1.0 the switch is one change. Flag-off behavior: actions
  disabled with a reason, not hidden (§7.4 principle).
- **Contract-error detection is shape validation on the response**, not status-code matching alone: an
  unexpected shape marks that action `ContractError`, disables it for the session with "Action unavailable —
  Microsoft API change", and logs the raw (redacted, §10.7) response for triage. Alternative — retrying or
  crashing — rejected by FR-5-AC-5.
- **Admin capability evaluated once per token** from role/`wids` claims (D-7), cached per account, re-evaluated
  on token refresh; unknown or absent claims → not capable (FR-5-AC-3). No Graph probe call — D-7's rationale
  (no extra consent, no audit-log noise) stands.
- **Reprovision path is structurally separate** from the other actions: it requires a `confirmed=true`
  argument the UI can only produce from the typed/checked dialog, is excluded from any batching or retry
  wrapper, and cancel returns before any network call (FR-5-AC-2). Making the dangerous path a different code
  path is cheaper than auditing a shared one.
- **Completion via §7.3 refresh** (D-8): after an accepted action, the service requests an immediate refresh,
  suppresses conflicting actions until the refresh resolves the new state (§7.4), and bounds the wait with a
  stated timeout after which the state is re-read rather than assumed.
- **National-cloud detection** at account level (cloud environment from the authority/issuer): where Cloud PC
  Graph APIs are absent (DOD, 21Vianet — §7.2), the whole FR-5 surface is disabled with the explanatory
  message (FR-5-AC-6), not per-action failures.

## Risks / Trade-offs

- [Beta contract changes under us mid-release] → feature flag + shape validation + per-action disable
  (FR-5-AC-5); fixtures with mutated shapes in CI (§13.3) so the degradation path itself is tested.
- [D-7 false negatives hide Restore/Resize from legitimate admins] → accepted by D-7; revisit trigger is
  "false negatives prove common in a real tenant" — log capability evaluations (redacted) so the frequency is
  measurable.
- [Reprovision is destructive and self-service] → typed/checked confirmation, separate code path, no
  batching/auto-retry (FR-5-AC-2, §10.6); the dialog text states the Cloud PC is wiped and rebuilt (§4.3).
- [Polling misses a fast transition or hangs on a slow one] → immediate post-action refresh plus bounded
  timeout (D-8); on timeout the UI shows last-known state with the action re-enabled only after a clean
  refresh.
- [Throttling under repeated actions] → honor `429 Retry-After` with exponential backoff, no user-visible
  error unless persistent (§9).

## Open Questions

- The stated post-action timeout value and the full status×action matrix are open item G-19 — the interim
  rule (unknown → disabled, timeout provisionally 60 s aligned to the §7.3 poll interval) must be replaced
  when G-19 lands.
