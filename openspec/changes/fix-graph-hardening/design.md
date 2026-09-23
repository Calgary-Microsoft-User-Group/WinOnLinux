# Design: fix-graph-hardening

## Context

`graph_client.graph_get_json` is already the single funnel for every Graph request, including nextLink pages —
which is what makes F-01 cheap to fix well: one validation site covers all present and future callers
(`cloudpc-actions` and `web-launcher` will reuse the same wrapper). The audit's failure scenario is not
hypothetical layering: D-4 rejects certificate pinning precisely because TLS-inspecting middleboxes are expected in
the target population, so a response-body attacker with a trusted CA is inside this client's stated threat model.

## Goals / Non-Goals

**Goals:**

- No bearer token is ever acquired for, or sent to, a URL that has not passed scheme/host validation.
- Every remote-payload malformation reachable from a 200 response maps into the existing typed taxonomy.
- Transient 5xx and oversized `Retry-After` values degrade gracefully inside the existing backoff budget.

**Non-Goals:**

- No allowlist configurability — the host set is `graph.microsoft.com`, hardcoded. National-cloud endpoints are out
  of scope until a requirement exists (revisit alongside the authority, which is equally hardcoded).
- No change to the 429 contract, the 30 s timeout, fresh-token-per-attempt (FR-4-AC-1), or the paging-fails-whole
  semantics (FR-1-AC-1) — all verified correct by the audit.
- No signature or content-integrity scheme for Graph responses (would re-argue D-4; not needed once the fetch
  target is validated).

## Decisions

- **Validate in `graph_get_json`, not in callers.** `urlsplit`; require `isinstance(url, str)`, scheme `https`,
  hostname exactly `graph.microsoft.com`. Reject before token acquisition, raising `GraphError` — the provider
  already maps that to `Failed`, so no new result type is needed. Exact host match, not suffix match:
  `evilgraph.microsoft.com.attacker.example` and `graph.microsoft.com.attacker.example` shapes die on exactness.
- **Parse errors map to the existing taxonomy rather than a new `ContractChanged` type.** `response.json()` moves
  inside the try with `ValueError` → `GraphError("non-JSON 200 response")`; the provider wraps its parse loop so
  `KeyError`/`TypeError` from a malformed entry becomes `Failed` carrying the previous entries. §9's "beta contract
  change" row asks for a distinguishable log line, which the wrapped exception message provides; a dedicated result
  type can be added when a UI wants to render it differently (none does yet).
- **5xx retry reuses the 429 loop.** Status in {502, 503, 504} retries with the same exponential backoff and the
  same budget; 500 stays fail-fast (it signals a server bug, not transience, and §9's taxonomy wants it surfaced).
  On budget exhaustion the last response surfaces as today's `GraphError`.
- **`Retry-After` ceiling = 300 s.** Values above it are clamped, and the clamp is logged. The alternative —
  surfacing `GraphThrottled` immediately above the ceiling — was rejected because no UI exists to render it yet and
  the field (`retry_after_seconds`) already survives on the error for when one does.

## Risks

- Amending §9's retry row is a normative-text edit; the tasks include it explicitly so spec and code do not diverge
  (the repo's recorded most-expensive failure mode).
- Exact-host validation would break if Microsoft ever pages across hosts. No documented Graph behavior does; if it
  appears, the allowlist gains an entry deliberately rather than the check loosening.
