# Tasks: add-connection-config-provider

## 1. Gate preconditions (blocking — no implementation below this line until all are checked)

- [ ] 1.1 Verify the Stage 0 finding document exists with outcome 1 or 2 (working identity, scope, consent path).
      On outcome 3 or D-14 timebox expiry: stop — web-only MVP, this change is not implemented.
- [ ] 1.2 Verify Gate LG-1's written position is recorded and covers the identity and capture method in use.
- [ ] 1.3 Verify Stage 1 outputs are checked in: feed schema (both legs), the feed→`.rdpw` field-mapping table, the
      staticness test result (T+0/+1 h/+24 h diff), and the §13.3 fixture set including the known-good `.rdpw`.
- [ ] 1.4 Verify Gate STACK is recorded closed (§11.1) — Stage 2 product code does not start before it.
- [ ] 1.5 Confirm risk-14 re-verification covered FreeRDP's signature-ignoring behavior (`arm.c`) at the pinned
      FreeRDP version — the premise of composition-without-signing.

## 2. Data model and interface

- [ ] 2.1 Define the `RdpwFile` and `Unavailable(reason)` types and the
      `getConnectionConfig(account, resourceRef)` interface (§5.1).
- [ ] 2.2 Define the fixed `.rdpw` field set from the Stage 1 field-mapping table (§13.3 field list) — the composer
      can emit these fields and no others.

## 3. Validation layer (§10.1)

- [ ] 3.1 Implement the Microsoft-domain-suffix allowlist and the host-shaped-field validator; violations are
      security errors that discard the config.
- [ ] 3.2 Implement type/shape validation for every non-host field; drop unknown fields; reject
      option-injection-shaped values.
- [ ] 3.3 Enforce strict TLS against the system trust store on the acquisition path, with no bypass surface (D-4).
- [ ] 3.4 Wire security-error reporting into the §9 taxonomy (distinct from connection errors; retry does not
      re-offer the rejected config).

## 4. Composition and caching

- [ ] 4.1 Implement the `.rdpw` composer over validated fields only (validate-then-compose).
- [ ] 4.2 Implement the D-5 in-memory cache keyed `(home account ID, resource ID)` with §5.2's invalidation
      triggers; no persistence.
- [ ] 4.3 Implement the handoff file: `0600`, user-private directory, app-generated path, deletion once the
      subprocess has started.

## 5. Tests (the CI story for the native path — §13.4)

- [ ] 5.1 Unit: valid fixture pair → composed `.rdpw` byte-compared against the known-good fixture.
- [ ] 5.2 Unit: each §13.3 hostile fixture fails closed as a security error (FR-2-AC-6).
- [ ] 5.3 Unit: cache semantics — session reuse, restart amnesia, every §5.2 invalidation trigger.
- [ ] 5.4 Unit: handoff-file permissions, path provenance, and deletion.
- [ ] 5.5 Code review: §10.1 and §10.4 controls (per §11.2's C verification), with the review recorded.

## 6. Integration

- [ ] 6.1 Wire `Unavailable(reason)` into the FR-2 disabled state and the one-action web fallback (FR-2-AC-5).
- [ ] 6.2 Integration (on-demand, live tenant): acquire a config for a running Cloud PC and confirm FreeRDP accepts
      the composed file (Stage 2 exit criterion, §11.1) within NFR-3's ≤ 3 s acquisition share.
