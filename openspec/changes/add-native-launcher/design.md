# Design: add-native-launcher

## Context

FreeRDP already implements Entra auth, ARM gateway resolution, reverse connect, and RDP/RDSTLS (§2.2); the
application's native path is: acquire token (§6) → obtain a validated `.rdpw` (§5.2, §8) → hand it to a FreeRDP
subprocess → manage the session lifecycle (§5.2). This design covers only the last two arrows. The integration mode
is already decided — subprocess, not linked library (D-1, §5.1.1) — and the client binary is already chosen —
`xfreerdp`, the X11 client (D-20, §5.6). Neither is re-argued here; both carry revisit triggers in §14.

Status of load-bearing facts, per the repo's verify-don't-assert convention:

- **Decided**: subprocess mode (D-1), `xfreerdp` (D-20), asyncio process supervision (D-18).
- **Decided but unverified**: FreeRDP 3.30.0 as the floor and its WebSocket fixes (§2.2 — post-cutoff, §12
  risk 14); the §5.5 flag names (verified against `xfreerdp --help` at Stage 3, not from memory).
- **Assumed until Stage 3**: exit-status granularity is sufficient for the §9 taxonomy (its insufficiency is D-1's
  revisit trigger; the full taxonomy is backlog item G-44).

## Goals / Non-Goals

**Goals:**

- FR-2-AC-4: cold start → visible native session on a running Cloud PC, no manual file handling, within NFR-3,
  TCP-only.
- Enforce NFR-8 (version floor) and the §10.1/§10.4 handoff and argument controls.
- Map subprocess outcomes onto the §9 error taxonomy well enough to drive the FR-2-AC-5 web-fallback surface.
- Propagate proxy configuration to the subprocess (§5.9).
- Ship the §5.5 Phase 1 redirection defaults and the §5.6 Phase 1 display scope.

**Non-Goals:**

- Embedding the session window in the application shell — priced and rejected in D-1.
- UDP Shortpath (Phase 2, §11.1) — sessions are TCP-only through the ARM gateway.
- Multi-monitor span, fractional scaling, per-monitor DPI (§5.6 — deferred, no Phase 1 commitment).
- Printers, smartcard, camera, USB redirection (§5.5 — deferred or out of scope).
- Native Wayland (§5.6 — the route there is the SDL backend, which is D-20's revisit path, not this change).
- Composing or validating the `.rdpw` — that is `add-connection-config-provider` (§5.2, §10.1).

## Decisions

1. **Supervision via asyncio subprocess APIs in the single event loop (D-18).** Each session is a task in the
   owning account's task group; blocking waits go through the executor. Alternative — a thread per child —
   contradicts D-18 and buys nothing the event loop does not already provide.
2. **Version probe at startup and re-probe before each launch.** `xfreerdp --version` output is parsed once and
   cached per binary path; NFR-8 requires the disabled-with-reason state, so the probe result is a first-class
   value (version, path, pass/fail) surfaced to the UI, not a boolean. The probe failing to parse is treated as
   below-floor (fail closed, §7.4's pessimism rule applied to versions).
3. **Handoff file lifecycle owned by the launcher, not the provider.** The provider returns `.rdpw` content in
   memory (D-5 cache is process-scoped); the launcher writes it `0600` under `$XDG_RUNTIME_DIR/<app>/` (app-generated
   name, §10.4), spawns the child, deletes the file once the child has started (§5.1.1), and guarantees deletion on
   every error path (context-manager semantics). Alternative — provider writes the file — spreads §10.1 hygiene
   across two components.
4. **Fixed argument template.** The vector is built from a literal list: binary, config path, `/gateway:type:arm`,
   `/sec:aad`, plus flags derived from typed per-resource settings (§5.5, §5.6) and proxy settings (§5.9). No
   feed-sourced string can introduce or alter an option because no feed-sourced string is ever an argument — the
   config file is the only channel (§10.4). Flag spellings live in one table, populated at Stage 3 from
   `xfreerdp --help`.
5. **Exit mapping is a two-level classification.** Level 1: spawn failure / early exit before window / exit after a
   session was established — determined from process lifetime and state. Level 2: known exit codes and stderr
   patterns (e.g. gateway rejection, `E_PROXY_ORCHESTRATION_LB_SESSIONHOST_DEALLOCATED` handled by FreeRDP's own
   retry) refine the §9 row. Unknown outcomes map to the generic session-failure row with logs captured (§10.7
   redaction applied). G-44 later replaces level 2's ad-hoc table; the interface (outcome enum → §9 row) is stable.
6. **Reconnect is re-acquisition.** A session drop invalidates the in-memory config (D-5 invalidation list, §5.2)
   and the reconnect prompt path re-runs provider → validate → launch. No stored config is ever reused across a
   drop (§9).
7. **Proxy propagation is explicit env/flag injection.** The launcher computes the effective proxy from the same
   source the HTTP stack uses (§5.9) and passes it to the child (environment and/or the verified FreeRDP proxy
   flag). Failure to connect through a proxy is reported as the §9 session-path-proxy row, distinct from offline.

## Risks / Trade-offs

- [Exit-status granularity proves too coarse for §9] → Recorded as D-1's revisit trigger; G-44 (exit-code taxonomy)
  is the escalation path, and stderr capture narrows the gap meanwhile.
- [§5.5 flag names differ at FreeRDP 3.30] → Single flag table, populated from `xfreerdp --help` at Stage 3 before
  any flag is relied on; a missing flag downgrades the capability with a reason, never a broken launch.
- [Handoff file lingers after a crash between write and delete] → Context-manager deletion on all paths plus
  startup sweep of the app's runtime dir; file content is routing metadata, not credentials, and lives in a
  user-private dir at `0600` (§10.1).
- [XWayland behavior surprises users on Wayland desktops] → §5.6 limitations are permanent Phase 1 position and
  must appear in user-facing documentation; Stage 3 measures rather than assumes them.
- [Two sessions contend for one config file] → Per-launch unique app-generated filenames (§10.4); NFR-5 requires
  concurrent sessions, so nothing is shared between children.

## Open Questions

- Live-session behavior when the client quits — deferred with trigger, decided at Stage 3 (§14.4).
- The deallocated-start timeout value — set alongside the §7.4 status × action matrix (G-19).
