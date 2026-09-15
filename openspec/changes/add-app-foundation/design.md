# Design: add-app-foundation

## Context

spec.md fixes the stack: Python 3 + GTK4/libadwaita via PyGObject (D-16), a single asyncio event loop with
per-account task groups (D-18), JSON state under `XDG_STATE_HOME` (D-13), Apache-2.0 (D-12). Those decisions are
**decided but unverified**: verification V3 (NFR-1 cold start, NFR-4 idle RSS measured against PyGObject) can
fire D-16's revisit trigger, and this change is where the measurements become possible. There is no application
code in the repository today; this change creates it.

## Goals / Non-Goals

**Goals:**

- A runnable GTK4/libadwaita application with application uniqueness (FR-4-AC-7).
- The asyncio↔GTK integration every async component will use, with per-account task groups whose cancellation
  semantics satisfy FR-3-AC-2's cancel-on-account-switch requirement.
- A persisted-state store with `schemaVersion` and forward-only migrations (D-13), holding non-secret state only.
- A logging layer that makes the §10.7 redaction rules structurally hard to bypass.
- A FreeRDP version probe usable by NFR-8 enforcement.
- A unit-test harness and CI that respects the §13.4 boundary (unit/fixture only in CI).

**Non-Goals:**

- No authentication, Graph, feed, launcher, or UI-shell functionality — those are separate changes.
- No Flatpak packaging (separate change); development runs from a venv against system GTK.
- No telemetry of any kind (D-11).

## Decisions

- **Event-loop bridge.** Use a GLib-integrated asyncio policy so GTK and asyncio share one thread and one loop
  (D-18). Alternatives considered: separate asyncio thread with `call_soon_threadsafe` marshalling — rejected
  because every UI update would cross threads, inviting the exact races D-18 exists to avoid.
- **Task-group registry keyed by home account ID.** A small registry object owns one task group per signed-in
  account plus one app-scoped group. Switching accounts cancels the outgoing account's group (FR-3-AC-2);
  signing out destroys it. Blocking calls (keyring, subprocess spawn) go through `loop.run_in_executor`.
- **Single instance via `Gio.Application` uniqueness.** GTK application IDs give second-launch activation
  nearly free (D-18 rationale); the second process forwards activation and exits, so two processes never share
  the token cache (FR-4-AC-7, §6.3 cross-process coordination).
- **State store as one JSON file per store, `schemaVersion` at the top.** Loading a newer version than the code
  understands fails safe (store treated read-only, user warned); loading an older version runs forward-only
  migrations then rewrites (D-13). Writes are atomic (temp file + rename). Tokens never pass through this layer.
- **Redaction as a logging filter, not caller discipline.** A filter installed on the root logger scrubs
  token-shaped values, `Authorization` headers, and `.rdpw` bodies at every level, and UPNs unless verbose
  diagnostics are explicitly enabled (§10.7). Callers cannot forget it because it is not opt-in.
- **Version probe = spawn `xfreerdp /version` and parse.** Runs in the executor, caches its result per app
  session, exposes `(present: bool, version: str | None, meets_floor: bool)`. The floor constant (3.30.0) lives
  here; the native launcher consumes the result (NFR-8). The flag/output shape is **assumed** until verified
  against FreeRDP 3.30 (§12 risk 14 discipline).

## Risks / Trade-offs

- [PyGObject cold start or RSS misses NFR-1/NFR-4] → instrument startup timing and RSS from the first commit;
  V3 measurement is a stated Gate STACK verification and D-16 names the fallback stack. Keeping the skeleton
  thin keeps the reversal affordable.
- [GLib-asyncio integration library churn] → isolate the bridge behind one module so a policy swap touches one
  file.
- [Version-probe output format differs across FreeRDP builds] → treat unparseable output as "present but
  unknown", which NFR-8 maps to native-disabled-with-reason rather than a crash.
- [State-store migration bugs corrupt preferences] → migrations are forward-only, versioned, and unit-tested
  per version step (D-13); atomic writes prevent partial files.

## Open Questions

- Which GLib-asyncio bridge (gbulb successor vs. hand-rolled `GLib.idle_add` pump) — decided at implementation
  time inside the isolated bridge module; the spec-level behavior does not change.
