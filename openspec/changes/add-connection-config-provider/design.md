# Design: add-connection-config-provider

## Context

The native path's stack is: acquire token (§6) → obtain a validated `.rdpw` (this provider) → hand it to a FreeRDP
subprocess (§5.1.1) → manage the session. What is **verified**: FreeRDP's documented AVD invocation pattern
(`<rdpw> /gateway:type:arm /sec:aad`). What is **decided but unverified** (risk 14 — re-verify at Stage 0):
FreeRDP's signature-ignoring behavior in `arm.c`, which is the premise that composition-without-signing works at
all. What is **assumed until Stage 1**: the feed's field schema and the feed→`.rdpw` field mapping, and the
staticness of the routing fields (risk 12 — Microsoft calls `loadbalanceinfo` a routing *token*, and §9 already
assumes configs can expire).

Because the client composes the file, the `.rdpw` signature is not a trust control here; §10.1's validation is.
That inversion is the central design fact of this module.

## Goals / Non-Goals

**Goals:**

- A single interface, `getConnectionConfig(account, resourceRef) -> RdpwFile | Unavailable(reason)`, returning a
  value rather than performing the launch — the property §13.4 needs for fixture-only CI coverage.
- Every connection-critical field validated before it is written into a configuration (§10.1).
- D-5 caching semantics exactly as specified, defaulting to the paranoid position while staticness is unverified.
- Handoff hygiene that keeps the config off disk except for the short-lived `0600` file (§10.1, §5.2).

**Non-Goals:**

- Launching or supervising FreeRDP — `add-native-launcher` (§5.1.1); this module never builds argument vectors.
- Feed protocol transport — `add-avd-feed-provider` owns discovery/download; this module consumes routing data.
- Certificate pinning — rejected by D-4, not deferred; do not re-argue it here.
- Signing the composed file — no signing authority exists or is needed (§2.2).

## Decisions

- **Value-returning provider, no side effects beyond the handoff file.** `getConnectionConfig` is the seam between
  everything network-dependent and everything testable: parser and validator are pure and run against fixtures in
  CI; only the thin acquisition wrapper touches the network. This is mandated by §13.4 and is why the interface is
  specified as returning a value (§5.1).
- **Validate-then-compose, never compose-then-filter.** Fields cross from feed model to `.rdpw` writer only through
  an explicit per-field validator: host-shaped fields pass the Microsoft-domain-suffix allowlist; every other field
  passes a type/shape check; unknown fields are dropped so the feed cannot inject configuration the client has not
  reasoned about (§10.1). The composed file contains exactly the fixed field set of §13.3, populated from validated
  values — feed data can never introduce a new property.
- **Allowlist content is evidence-based.** The domain-suffix allowlist ships from the Stage 1 capture evidence plus
  Microsoft's published endpoint documentation; any addition is a reviewed change, not a runtime override. There is
  deliberately no user-facing "continue anyway" (§10.1).
- **Security errors are terminal per config.** An allowlist or shape violation discards the config, reports a
  security error (FR-2-AC-6, §9), and marks the cached entry poisoned so a retry re-fetches rather than re-offering
  the same rejected config.
- **Cache per D-5**: keyed `(home account ID, resource ID)`, process-lifetime, in-memory only. Invalidation
  triggers exactly §5.2's list: token change or re-auth, observed resource state change, any feed or auth error,
  any gateway rejection during connect. No TTL exists until the Stage 1 staticness test passes and states one.
- **Handoff file**: created with `0600` permissions in a user-private runtime directory at an app-generated path
  (never derived from remote values — §10.4), deleted once the subprocess has started; reconnects re-acquire and
  re-validate rather than reusing a file (§9).

## Risks / Trade-offs

- [Routing fields prove non-static and cached configs fail intermittently — risk 12, the worst class to diagnose] →
  D-5's defaults assume non-static: no persistence, re-fetch on first launch per session, invalidation on every
  gateway rejection. The Stage 1 staticness test (T+0/+1 h/+24 h diff) is the only path to relaxing this.
- [Feed tampering steers a session, with its Entra token, to an attacker host — risk 11] → strict TLS + system
  trust store + host allowlist + shape validation; hostile fixtures make the defense a regression-tested behavior,
  not a review-time promise. Accepted residual (D-4): a CA already in the system trust store defeats transport
  identity; integrity then rests entirely on field validation.
- [The signature-ignoring premise fails re-verification (FreeRDP starts verifying `.rdpw`)] → the native path as
  designed is blocked; this is a Stage 0/volatile-facts check (risk 14) and a D-1-adjacent revisit, escalated
  rather than patched here.
- [Allowlist too narrow for some region/cloud, valid configs rejected] → allowlist failures log the offending
  suffix (redacted per §10.7) so field reports are diagnosable; additions ship as reviewed releases. Trade accepted:
  fail-closed beats fail-open for the component holding §10.1.
- [NFR-3's ≤ 3 s config-acquisition share missed on first launch] → first launch pays full acquisition by design
  (D-5); measured at Stage 3, and only the staticness result can buy more headroom.

## Open Questions

- The exact feed→`.rdpw` field-mapping table — a Stage 1 deliverable this design consumes.
- Whether `Unavailable(reason)` needs reason subcodes beyond §9's taxonomy for the launcher's retry heuristics —
  settle during Stage 3 integration.
