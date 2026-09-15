# Tasks: add-app-foundation

## 1. Repository scaffold

- [ ] 1.1 Create `pyproject.toml` (project metadata, PyGObject dependency, pytest), `src/winonlinux/` package
      layout, and Apache-2.0 `LICENSE` (D-12)
- [ ] 1.2 Add pytest harness with a `unit` marker as the CI default and an opt-in marker for anything needing
      network/display, enforcing the §13.4 boundary
- [ ] 1.3 Add CI workflow running lint + unit/fixture tests only on a headless, network-isolated runner

## 2. Application shell and event loop

- [ ] 2.1 Implement `Adw.Application` entry point with a stable application ID and uniqueness; second launch
      activates the running window and exits (FR-4-AC-7)
- [ ] 2.2 Implement the GLib↔asyncio bridge module (single loop, one thread) behind its own interface (D-18)
- [ ] 2.3 Implement the task-group registry: one group per home account ID plus an app-scoped group; cancel and
      destroy semantics; thread-executor helper for blocking calls
- [ ] 2.4 Unit tests: group cancellation isolation; executor offload keeps the loop responsive; uniqueness
      manual test documented (FR-4-AC-7 M-verification per §11.2)

## 3. State store

- [ ] 3.1 Implement JSON store under `XDG_STATE_HOME` with `schemaVersion`, atomic temp-file+rename writes, and
      forward-only migration runner (D-13)
- [ ] 3.2 Refuse secret-shaped values at the store API; log refusals redacted
- [ ] 3.3 Unit tests: migration from version 0, newer-version fail-safe, atomicity on simulated crash,
      secret-rejection

## 4. Logging and redaction

- [ ] 4.1 Implement central logging configuration with a redaction filter covering tokens, `Authorization`
      headers, `.rdpw` bodies (all levels) and UPNs (default level) per §10.7
- [ ] 4.2 Unit tests: each redaction class at each level, including format-string and structured-args paths

## 5. FreeRDP version probe

- [ ] 5.1 Implement probe: locate `xfreerdp`, invoke its version output in the executor, parse against the
      3.30.0 floor, cache per session; expose `(present, version, meets_floor)` (NFR-8)
- [ ] 5.2 Unit tests with faked subprocess output: present-and-ok, below-floor, absent, unparseable → present
      but unknown
- [ ] 5.3 Record that the exact version-output shape is assumed until checked against a real FreeRDP 3.30
      build (§12 risk 14); add the check to the Stage 3 manual plan reference

## 6. NFR instrumentation

- [ ] 6.1 Record cold-start-to-interactive timing at startup (NFR-1) and document the RSS measurement
      procedure (NFR-4) for verification V3
- [ ] 6.2 Capture a first V3 baseline measurement and file the result against Gate STACK (fires D-16's revisit
      trigger if failing)
