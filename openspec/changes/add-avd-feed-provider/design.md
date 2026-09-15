# Design: add-avd-feed-provider

## Context

The AVD workspace feed is the project's critical-path unknown (§8, §12 risk 4). What is **verified** (as of the
August 2026 research pass, itself flagged for Stage 0 re-verification per risk 14): the discovery URL is published;
Microsoft documents a two-stage flow (initial feed discovery → workspace feed download); the token audience is
`https://www.wvd.microsoft.com/.default`; no public client-side implementation of the ARM feed protocol exists.
What is **decided but unverified**: that the project's own Entra registration can obtain the feed audience — the
Stage 0 gate, with the first-party AVD client ID as documented fallback. What is **assumed**: the request/response
schema of both legs and the chaining between them — that knowledge arrives only from the Stage 1 capture (risk 4a
records that the capture method itself may fail, in which case D-14's web-only commitment applies).

This provider therefore has an unusual shape: its *contract* can be specified now (inputs, outputs, error taxonomy,
fixture obligations), but its *wire format* is a Stage 1 deliverable. Code is written against the captured schema
and the checked-in fixtures, never against guesses.

## Goals / Non-Goals

**Goals:**

- Enumerate AVD workspaces, desktops and RemoteApps for the active account (FR-1-AC-5), grouped by workspace.
- Expose the raw workspace-feed routing data to the Connection-Config Provider without leaking feed internals to
  any other consumer (§5.1: "consumers never see feed internals").
- Map every feed failure onto the §9 taxonomy, keeping the Cloud PC section fully functional throughout.
- Make the parser and grouping logic exercisable purely from fixtures, with no network (§13.4).

**Non-Goals:**

- `.rdpw` composition and field validation — owned by `add-connection-config-provider` (§5.2, §10.1).
- Anything below the connection config: Entra auth for the session, ARM gateway, reverse connect, RDP — FreeRDP
  implements all of it (§2.2) and none of it is reimplemented here.
- The Stage 1 capture itself (schema document, field-mapping table, fixture acquisition) — that is sprint/gate work
  tracked outside this change; this change *consumes* its outputs.
- Caching feed responses across sessions — the staticness question (risk 12) belongs to the config provider's D-5
  rules; this provider fetches on demand.

## Decisions

- **Implement against the captured schema only.** The parser's authoritative input is the Stage 1 schema document
  plus the redacted fixtures checked into the repository. Any field not present in the capture is treated as
  unknown. Alternative — inferring the schema from MS-TSWP or RAWeb — rejected: both are documented *relatives* of
  the ARM feed, not the protocol itself (§8), and building on them would launder an assumption into code.
- **Identity is injected, not chosen here.** The provider requests tokens from the Auth/Account Manager naming the
  feed audience; which client ID backs that request is the Stage 0 finding, configured in one place. This keeps
  outcome 2 (first-party client-ID fallback) a configuration change rather than a code change.
- **Silent acquisition precedes every feed call** (FR-4-AC-1); the provider never caches a token and never derives
  timing from assumed lifetimes.
- **Feed `401` after successful issuance is an identity-model failure, not expiry** (§9). It does not enter the
  ReauthRequired path; it yields `Unavailable(reason)` toward the native path, logs the audience actually issued,
  and leaves web launch as the offered route.
- **Parser as a pure function**: bytes in, typed resource model out, no I/O — this is what §13.4 requires for CI
  coverage, and it is the same shape that lets hostile-fixture testing work in the config provider.
- **Proxy honoring** (§5.9): the provider uses the application's shared HTTP stack, which respects
  `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` and desktop proxy settings; proxy rejection is reported as a proxy failure,
  never as plain offline.

## Risks / Trade-offs

- [The wire schema is undocumented and may shift under us at any time — risk 4] → implement only against captured
  fixtures; unknown fields ignored by the parser; schema drift surfaces as a parse error mapped to the §9 inline
  feed error, with the raw (redacted) response logged for re-capture. The volatile-facts register (risk 14) carries
  the re-verification obligation.
- [Stage 1 capture may fail entirely — risk 4a] → pre-committed: outcome is web-only MVP per D-14. The gate tasks in
  `tasks.md` block implementation from starting without Stage 1 outputs, so no code is written on speculation.
- [Feed token may be device-bound or ungrantable to our registration — Stage 0 outcome 3] → same pre-commitment;
  the provider is not built.
- [First-party client-ID fallback carries ToS exposure — §12] → Gate LG-1's written position is a task-level
  precondition; the identity-injection decision above keeps the exposure revocable.
- [Enumeration latency joins the NFR-2 budget (≤ 3 s for ≤ 25 resources)] → discovery and workspace downloads run
  concurrently per workspace under the D-18 asyncio model; measured at Phase 1 exit.

## Open Questions

- Whether the discovery response returns one workspace-feed URL or several (per region/workspace) — answered by the
  Stage 1 capture; the provider models a list either way.
- Pagination or partial-response behavior of the workspace feed — unknown until capture; fixtures must include
  whatever shape is observed.
