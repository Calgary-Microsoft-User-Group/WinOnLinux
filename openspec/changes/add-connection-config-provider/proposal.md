# Proposal: add-connection-config-provider

> **Gate status — read first.** This change is blocked on the same three gates as `add-avd-feed-provider`:
> **Stage 0** (app-identity spike, 3-working-day hard timebox, D-14), **Gate LG-1** (written legal position), and
> **Stage 1** (feed schema capture, field-mapping table, staticness test, checked-in fixtures). It is additionally
> preceded by **Gate STACK** (§11.1), which Stage 2 requires. A negative gate outcome means **web-only MVP per
> D-14** — pre-committed, not worked around.

## Why

Everything below the connection configuration is already implemented by FreeRDP — Entra auth, ARM gateway
negotiation, reverse connect, RDP/RDSTLS (§2.2). The one gap in the native path (§5.2, §8) is producing a valid
`.rdpw` for a chosen resource. Because FreeRDP does not verify the `.rdpw` signature, this client *composes* the
file from workspace-feed data, and the integrity burden Microsoft's signature would have carried moves onto this
client's validation (§10.1). This provider is "the only genuinely new logic in the native path" (§5.1) and is what
Stage 2 exists to build; without it FR-2-AC-4 (native cold-start session) cannot be met.

## What Changes

- New **Connection-Config Provider** implementing
  `getConnectionConfig(account, resourceRef) -> RdpwFile | Unavailable(reason)` (§5.1): resolves the regional feed
  endpoint, obtains workspace-feed routing data via the AVD Feed provider, validates every field, and composes a
  `.rdpw` containing the fields FreeRDP reads: `gatewayhostname`, `loadbalanceinfo`, `armpath`, `geo`,
  `full address`, `remoteapplicationprogram` (§13.3).
- **Trust controls replacing the signature** (§10.1): strict TLS on all feed calls with no user-facing bypass;
  validation against the system trust store with certificate pinning rejected (D-4); allowlist validation of every
  host-shaped field against expected Microsoft domain suffixes; type/shape validation of every other field with
  unknown fields dropped. An allowlist violation is a **security** error (FR-2-AC-6): the config is discarded, no
  session is attempted, and retry does not re-offer the same config.
- **Caching per D-5** (§5.2): in-memory per `(account, resource)` for the app-process lifetime only; re-fetch on
  first launch of each resource per session; never persisted to disk; invalidated on token change/re-auth, observed
  resource state change, any feed or auth error, and any gateway rejection. Staticness is unverified (risk 12), so
  these rules relax only if the Stage 1 staticness test passes, and any relaxation must state a TTL.
- **Handoff hygiene** (§10.1, §5.2): the composed file is written `0600` in a user-private directory, at an
  app-generated path, and deleted once the FreeRDP subprocess has started.
- **Hostile-fixture test surface** (§13.3): out-of-allowlist `full address`, malformed `gatewayhostname`, unknown
  injected field, option-injection-shaped values — all must be rejected, and the parser/validator must run in CI
  purely from fixtures with no network (§13.4).
- `Unavailable(reason)` drives the FR-2 disabled state and the one-action web fallback (FR-2-AC-5); consumers never
  see feed internals.

## Capabilities

### New Capabilities

- `connection-config`: acquisition, validation, composition, caching and handoff of native connection
  configurations — the trust boundary between feed data and the FreeRDP subprocess.

### Modified Capabilities

<!-- none — no existing deployed capability changes -->

## Impact

- **Code**: new provider module between the resource layer and the connection launchers (§5.1).
- **Dependencies**: `add-avd-feed-provider` (workspace-feed routing data), Auth/Account Manager (silent acquisition
  before every acquisition, FR-4-AC-1), Stage 1 outputs (schema, field-mapping table, staticness result, fixtures).
- **Consumers**: `add-native-launcher` receives the `.rdpw` path; the UI receives `Unavailable(reason)` for the
  FR-2 disabled state and web fallback.
- **Security posture**: this module carries §10.1 and half of §10.4 — it is the component the threat model's
  feed-tampering scenario (risk 11) is defended in. Accepted residual per D-4: an adversary holding a CA the system
  already trusts is outside detection.
- **Verification**: FR-2-AC-6 and the §10.1/§10.4 controls verify by unit tests on hostile fixtures plus code
  review (§11.2).
