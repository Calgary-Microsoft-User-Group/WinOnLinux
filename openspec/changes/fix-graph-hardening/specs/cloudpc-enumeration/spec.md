# cloudpc-enumeration — Graph remote-data hardening

Hardens the Graph call wrapper and provider parse path specified by `add-cloudpc-enumeration` against
remote-supplied URLs and malformed payloads, and widens the retry contract (audit 2026-09-22 findings F-01, F-03,
F-16, F-17; spec.md §9, §10.1).

## ADDED Requirements

### Requirement: No token is sent to an unvalidated URL

The Graph call wrapper SHALL validate every request URL — including `@odata.nextLink` values taken from response
bodies — before acquiring or attaching an access token: the URL MUST be a string with scheme `https` and host
exactly `graph.microsoft.com`. A URL failing validation SHALL raise the typed Graph error without any token being
acquired for the attempt (§10.1 host-allowlist principle).

#### Scenario: Hostile nextLink

- **WHEN** a page response contains `"@odata.nextLink": "https://attacker.example/x"`
- **THEN** no further HTTP request is made, no token is acquired for it, and the refresh surfaces as `Failed` with
  the previous list retained (FR-1-AC-4)

#### Scenario: Downgraded scheme

- **WHEN** a nextLink value is `http://graph.microsoft.com/...` or a non-string JSON value
- **THEN** it is rejected identically

### Requirement: Malformed Graph payloads surface as typed failures

A 200 response whose body is not JSON, whose `value` is not a list, or whose entries lack required fields (`id`)
SHALL surface through the typed result taxonomy — `GraphError` from the wrapper, `Failed` from the provider — and
SHALL NOT escape as an untyped exception (§9 "beta contract change").

#### Scenario: Entry missing id

- **WHEN** one entry in a `/me/cloudPCs` page lacks the `id` field
- **THEN** the refresh returns `Failed` (previous list retained), logs a contract-change line, and no `KeyError`
  reaches the caller or the poll loop

#### Scenario: Non-JSON 200 body

- **WHEN** Graph returns HTTP 200 with a body that fails JSON parsing
- **THEN** the wrapper raises the typed Graph error, not `JSONDecodeError`

### Requirement: Transient 5xx responses are retried within the backoff budget

HTTP 502, 503, and 504 SHALL be retried with the same exponential backoff and retry budget as 429. HTTP 500 SHALL
surface immediately as today. Exhausting the budget SHALL surface the typed Graph error.

#### Scenario: Single transient 503

- **WHEN** a request returns 503 and the retry returns 200
- **THEN** the refresh succeeds with no user-visible failure

### Requirement: Honored Retry-After is capped

An integer `Retry-After` value SHALL be honored up to a stated ceiling (300 s); larger values SHALL be clamped to
the ceiling and the clamp logged. Absent or unparseable values continue to fall back to exponential backoff.

#### Scenario: Oversized Retry-After

- **WHEN** a 429 response carries `Retry-After: 3600`
- **THEN** the wait before the next attempt does not exceed the ceiling
