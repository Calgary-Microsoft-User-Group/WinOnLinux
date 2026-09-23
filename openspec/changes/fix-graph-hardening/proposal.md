# Proposal: fix-graph-hardening

## Why

The 2026-09-22 code audit (`codeaudit/2026-09-22.md`) found one HIGH defect in the applied Graph path: the
`@odata.nextLink` paging URL is remote-supplied, and `graph_get_json` attaches a freshly acquired bearer token to
whatever URL it is handed with no scheme or host validation (F-01). Under the middlebox conditions D-4 explicitly
designs for, that is a token-exfiltration channel. Three MEDIUM/LOW resiliency gaps live in the same two modules:
malformed 200-responses escape the typed result taxonomy as bare `KeyError`/`JSONDecodeError` (F-03), transient
5xx responses are never retried (F-16), and an honored `Retry-After` value is uncapped, so a single header can hang
a refresh for hours (F-17). Fixing them together keeps the change surface to `graph_client.py`,
`cloudpc_provider.py`, and their tests.

## What Changes

- **Validate every URL before a token is attached** (F-01): `graph_get_json` becomes the single choke point that
  requires a `str` URL with scheme `https` and host exactly `graph.microsoft.com` before acquiring or sending a
  token; anything else raises `GraphError` without a token ever being acquired for it (§10.1 host-allowlist
  principle).
- **Malformed remote data becomes a typed failure, never an untyped crash** (F-03): a 200 body that is not JSON, a
  `value` that is not a list, or an entry missing `id` surfaces as `Failed` (provider) / `GraphError` (client) per
  §9's "beta contract change" row — one malformed entry no longer escapes the `EnumerationResult` contract.
- **Retry transient 5xx** (F-16): 502/503/504 join 429 inside the existing backoff budget (1 s → 30 s cap,
  `max_retries` unchanged). This amends §9's retry row, which currently mandates retry for 429 only.
- **Cap honored `Retry-After`** (F-17): values above a stated ceiling are clamped, so a hostile or misconfigured
  header cannot hang the poll loop for its full duration.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `cloudpc-enumeration`: adds remote-URL validation, typed malformed-payload handling, 5xx retry, and the
  `Retry-After` ceiling to the Graph call wrapper and provider parse path.

## Impact

- Code: `src/winonlinux/graph_client.py`, `src/winonlinux/cloudpc_provider.py`; tests for all four behaviors.
- Spec: §9's retry row is amended (5xx joins 429; `Retry-After` ceiling stated). Per CLAUDE.md, the amendment is
  applied in this change, not deferred.
- Closes audit findings F-01 (HIGH), F-03, F-16, F-17. No decision register entry is reversed; F-01's remediation
  is the §10.1 allowlist principle applied to a fetch target, and coexists with D-4 (no pinning).
- No dependency on unapplied changes; safe to implement immediately. A Linear issue in `WinOnLinuxCode` should be
  filed and linked per repo convention.
