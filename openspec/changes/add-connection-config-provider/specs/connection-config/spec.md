# connection-config — delta specification

## ADDED Requirements

### Requirement: Connection configurations are acquired through a single value-returning interface

The provider SHALL expose `getConnectionConfig(account, resourceRef)` returning either a composed `.rdpw`
configuration or `Unavailable(reason)` (§5.1). A silent token acquisition for the owning account SHALL immediately
precede every acquisition (FR-4-AC-1). Consumers SHALL NOT observe feed internals; `Unavailable(reason)` SHALL
drive the FR-2 disabled state and the one-action web fallback (FR-2-AC-5).

#### Scenario: Valid feed data yields a complete configuration

- **WHEN** `getConnectionConfig` is called for a resource whose workspace-feed routing data passes validation
- **THEN** it returns a `.rdpw` containing every field FreeRDP reads — `gatewayhostname`, `loadbalanceinfo`,
  `armpath`, `geo`, `full address`, and `remoteapplicationprogram` where applicable (§13.3)

#### Scenario: Acquisition failure disables native with a reason

- **WHEN** feed data cannot be obtained or validated for a resource
- **THEN** the provider returns `Unavailable(reason)`, the native method is shown disabled with that specific
  reason (FR-2-AC-1), and web fallback is offered in the same failure surface (FR-2-AC-5)

### Requirement: Host-shaped fields are validated against a Microsoft-domain allowlist

Every host-shaped field consumed from the feed SHALL be validated against expected Microsoft domain suffixes
before it is written into a configuration (§10.1). A value outside the allowlist SHALL be reported as a
**security** error (FR-2-AC-6, §9), the composed configuration SHALL be discarded, no session SHALL be attempted,
and a retry SHALL NOT re-offer the same configuration.

#### Scenario: Out-of-allowlist full address is rejected as a security error

- **WHEN** a feed response carries a `full address` outside the Microsoft domain-suffix allowlist (§13.3 hostile
  fixture)
- **THEN** the configuration is rejected and reported as a security error, no session is attempted, and a
  subsequent retry re-fetches rather than re-offering the rejected configuration

#### Scenario: Malformed gatewayhostname is rejected

- **WHEN** a feed response carries a malformed `gatewayhostname` (§13.3 hostile fixture)
- **THEN** validation fails, the configuration is discarded, and the failure is reported as a security error, not a
  connection error

### Requirement: Non-host fields are shape-validated and unknown fields are dropped

Every non-host field consumed from the feed SHALL pass type and shape validation before composition, and fields the
client has not reasoned about SHALL be dropped rather than passed through (§10.1). Feed-sourced values SHALL never
introduce, extend, or alter a configuration property outside the fixed set the application composes (§10.4).

#### Scenario: Unknown injected field does not reach the configuration

- **WHEN** a feed response contains an unknown injected field (§13.3 hostile fixture)
- **THEN** the composed `.rdpw` does not contain it and composition otherwise proceeds

#### Scenario: Option-injection-shaped value is neutralized

- **WHEN** a feed response contains a value shaped to smuggle an option or directive (§13.3 hostile fixture)
- **THEN** the value fails shape validation, the configuration is rejected as a security error, and no session is
  attempted

### Requirement: Feed transport uses strict TLS with no bypass

All feed calls made for configuration acquisition SHALL validate TLS against the system trust store (D-4) and
SHALL offer no user-facing bypass for certificate errors (§10.1). Certificate pinning SHALL NOT be used.

#### Scenario: Certificate error yields Unavailable, never a bypass prompt

- **WHEN** TLS validation fails on a feed call
- **THEN** the provider returns `Unavailable` with the TLS failure as the reason, and no "continue anyway" option
  is presented anywhere in the flow

### Requirement: Configurations are cached in memory per session and never persisted

The provider SHALL cache configurations in memory per `(account, resource)` for the application-process lifetime
only, SHALL re-fetch on the first launch of each resource per app session (D-5), SHALL keep nothing on disk beyond
the short-lived handoff file, and SHALL invalidate on: account token change or re-auth, an observed resource state
change, any feed or auth error, and any gateway rejection during connect (§5.2). These rules SHALL NOT be relaxed
unless the Stage 1 staticness test passes, and any relaxation MUST state a TTL.

#### Scenario: Second launch in a session reuses the cache

- **WHEN** a resource is launched natively a second time within the same app session with no invalidation trigger
  in between
- **THEN** the cached configuration is reused with no re-fetch, fitting the NFR-3 acquisition budget

#### Scenario: App restart forgets all configurations

- **WHEN** the application is restarted
- **THEN** no configuration survives from the previous session and the next launch of any resource re-fetches

#### Scenario: Gateway rejection invalidates the cached configuration

- **WHEN** a native connect is rejected by the gateway using a cached configuration
- **THEN** that cache entry is invalidated and the next attempt re-fetches and re-validates

### Requirement: Handoff files are private, transient, and app-named

A composed configuration SHALL be written to a file with `0600` permissions in a user-private directory, at a path
generated by the application and never derived from any remote value (§10.4), and SHALL be deleted once the FreeRDP
subprocess has started (§10.1). Reconnects SHALL re-acquire and re-validate the configuration (§9).

#### Scenario: Handoff file lifecycle

- **WHEN** a native launch hands a configuration to the FreeRDP subprocess
- **THEN** the file is created `0600` in a user-private directory at an app-generated path and is deleted once the
  subprocess has started

### Requirement: Parser and validator run in CI purely from fixtures

The provider's parser and validator SHALL be exercisable with no network, from checked-in fixtures: the valid
captured feed pair, the known-good composed `.rdpw`, and the hostile set — out-of-allowlist `full address`,
malformed `gatewayhostname`, unknown injected field, and option-injection-shaped values (§13.3, §13.4).

#### Scenario: Hostile fixtures all fail closed in CI

- **WHEN** the unit suite runs each §13.3 hostile fixture through the provider offline
- **THEN** every hostile fixture is rejected as a security error and the valid fixture composes a `.rdpw` matching
  the known-good file
