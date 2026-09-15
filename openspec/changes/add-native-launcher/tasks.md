# Tasks: add-native-launcher

## 1. Prerequisites and fact re-verification

- [ ] 1.1 Confirm Stage 1 (feed schema) and Stage 2 (Connection-Config Provider) are complete; this change is
      Stage 3 work (§11.1) and cannot start before its inputs exist
- [ ] 1.2 Re-verify FreeRDP 3.30.0 release contents and the upstream AVD invocation pattern against current
      upstream (§12 risk 14)
- [ ] 1.3 Build the §5.5/§5.6 flag table from `xfreerdp --help` at the bundled 3.30 binary — every flag verified,
      none from memory; record any renames or absences

## 2. Version floor (NFR-8)

- [ ] 2.1 Implement binary discovery and `--version` probing with parse-failure treated as below-floor
- [ ] 2.2 Surface the probe result (version, path, pass/fail) as a typed value the UI renders into the
      disabled-with-reason state; probe at startup and re-probe before each launch
- [ ] 2.3 Unit tests: below-floor, absent, unparseable, at-floor; manual test §13.5 item 5 (downgrade and remove)

## 3. Handoff and argument handling (§10.1, §10.4)

- [ ] 3.1 Implement the handoff-file context manager: app-generated unique path under the user-private runtime
      dir, `0600`, delete-after-start, delete-on-all-error-paths, startup sweep of leftovers
- [ ] 3.2 Implement the fixed argument-vector builder: binary, config path, `/gateway:type:arm`, `/sec:aad`, typed
      settings flags only; no shell anywhere
- [ ] 3.3 Unit tests with injection-shaped feed values (§13.3): assert the vector contains only fixed options and
      the hostile value never reaches an argument

## 4. Launch and supervision

- [ ] 4.1 Implement subprocess launch and tracking as asyncio tasks in the owning account's task group (D-18)
- [ ] 4.2 Implement the two-level exit classifier (spawn failure / pre-window exit / post-session exit, then known
      code and stderr refinement) mapping onto the §9 taxonomy; unknown outcomes → generic session failure with
      redacted logs (§10.7)
- [ ] 4.3 Implement deallocated-host starting state: progress UI over FreeRDP's orchestration retry, bounded by
      the §7.4 timeout (value from G-19)
- [ ] 4.4 Implement session-drop handling: invalidate the D-5 in-memory config, reconnect prompt re-runs
      acquire → validate → launch
- [ ] 4.5 Wire every native failure surface to offer web fallback in one action (FR-2-AC-5)
- [ ] 4.6 Support ≥ 2 concurrent sessions with per-child state; soft-warn above 4 (NFR-5)

## 5. Proxy propagation (§5.9)

- [ ] 5.1 Compute the effective proxy from the same source the HTTP stack uses and inject it into the child's
      environment/flags (flag verified in 1.3)
- [ ] 5.2 Map proxied-session failures to the distinct §9 session-path-proxy row; integration test the
      auth-succeeds-connect-fails case behind a proxy

## 6. Redirection and display (§5.5, §5.6)

- [ ] 6.1 Implement Phase 1 defaults (clipboard text on, audio out on, everything else off/deferred) and the
      per-resource folder picker as the only drive-redirection form
- [ ] 6.2 Present redirection as negotiated; host-refused channels reported informationally
- [ ] 6.3 Wire `/dynamic-resolution`, windowed/fullscreen, integer scaling; single monitor only
- [ ] 6.4 Document the §5.6 XWayland limitations in user-facing docs as the permanent Phase 1 position

## 7. Stage 3 exit evidence

- [ ] 7.1 Run the §13.5 manual test plan (cold start, deallocated wait, mid-session kill, hostile fixtures,
      version floor, platform matrix) and record results
- [ ] 7.2 Measure and record FR-2-AC-4 against the NFR-3 budget
- [ ] 7.3 Run the NFR-6 TCP-only acceptability procedure and record the result — it gates Phase 1 completion and
      prioritizes Phase 2
- [ ] 7.4 Record the §5.6 display-backend confirmation (measured Wayland/XWayland limitations vs the table)
