# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this repo is

WinOnLinux — a Linux desktop client for Windows 365 Cloud PCs and Azure Virtual Desktop. The repo started as
**specification-only** and is now transitioning into a **live coding repo**: `openspec/changes/` holds 10 OpenSpec
change proposals (`add-app-foundation`, `add-auth-account-manager`, `add-avd-feed-provider`, `add-cloudpc-actions`,
`add-cloudpc-enumeration`, `add-connection-config-provider`, `add-flatpak-packaging`, `add-native-launcher`,
`add-ui-shell`, `add-web-launcher`), each with a `proposal.md`, `design.md`, `specs/` delta, and a `tasks.md` of real
implementation tasks (Python/GTK4/FreeRDP code, tests, CI). As of 2026-09-14 none has been applied yet — no
`src/` tree exists and there are still no build/test/lint commands to run — but do not assume "document work only":
once a change is applied (see the `openspec-apply-change` skill), work in this repo includes writing and testing
application code against that change's `tasks.md`, not just editing markdown. `spec.md` stays normative for
requirements and decisions throughout; OpenSpec changes implement it, they don't replace it as the source of truth.

```
spec.md                   The specification. §14 is the decision register — authoritative.
gapsandrecommendations.md  Completeness/feasibility assessment: 54 findings (G-nn), applied + backlog.
README.md                  Public-facing summary.
openspec/changes/*/        OpenSpec change proposals decomposing spec.md into implementable units (see above).
docs/superpowers/specs/2026-08-18-...-design.md
                           Original native-path design. Partly superseded by spec.md.
```

Execution is tracked in **Linear** — team `BigHatGroup` (workspace `linear.app/bighatgroup`), deliberately split four ways:

| Project (real Linear name) | Holds | Do not put |
| --- | --- | --- |
| `WindowsAppforLinuxPrereq` | Tenant, licence, hardware, tooling procurement (E-1…E-11) | Spec or code work |
| `WindowsAppForLinuxImpl` | Open specification and decision gaps (G-nn) | Procurement or execution |
| `WindowsAppForLinuxSprint1` | The 5-day feasibility sprint (S-0…S-7, V3, LG-1) | Anything beyond the sprint |
| `WinOnLinuxCode` | Application code, one issue per OpenSpec change under `openspec/changes/` (10/10 filed as of 2026-09-15); ordering enforced by Linear blocking relations, Phase 1 issues titled `(GATED: Stage 0/1, LG-1)` | Spec/decision gaps or procurement |

Older references to "Prerequisites", "Implementation", and "Sprint 1" project names elsewhere (including earlier in this file's history) mean
`WindowsAppforLinuxPrereq`, `WindowsAppForLinuxImpl`, and `WindowsAppForLinuxSprint1` respectively — those are the actual Linear project
names to use when searching, filtering, or linking issues.

## The five technical facts most easily got wrong

1. **FreeRDP already does everything below the connection config** — Entra auth, ARM gateway negotiation, reverse
   connect, RDP/RDSTLS. Do not propose reimplementing any of it. The gap is *one call chain*: feed discovery →
   workspace feed download → compose a `.rdpw`.
2. **FreeRDP does not verify the `.rdpw` signature.** The client *composes* the file from feed data; no signing
   authority is needed. This is why the native path is tractable — and why §10.1 puts the integrity burden on host
   allowlisting and field validation instead of on a signature. If you see text implying the config is a pre-signed
   artifact passed through opaquely, that is stale.
3. **Graph cannot substitute for the feed.** Graph is control-plane only: enumeration, management actions, and a
   *web-launch* URL. The connection-critical fields exist only in the AVD feed.
4. **Graph cannot enumerate AVD resources for an end user.** AVD objects live under ARM and need Azure RBAC ordinary
   users lack. That is why AVD enumeration depends on the feed work.
5. **`xfreerdp` is an X11 client.** On Wayland, sessions always run through XWayland. The §5.6 limitations are
   permanent for Phase 1, not awaiting better Wayland support.

## Working conventions

**The decision register (`spec.md` §14) is normative.** Twenty decisions, D-1…D-20, each with rationale and a revisit
trigger. Before proposing an architectural change, check whether it is already decided — and if you want to reverse
one, cite its revisit trigger rather than re-arguing from scratch. Decided so far: subprocess FreeRDP (D-1), keyring
required with no file fallback (D-2), Flatpak (D-3), no certificate pinning (D-4), in-session config caching (D-5),
Python 3 + GTK4 (D-16), MSAL Python (D-17), asyncio (D-18), `xfreerdp` (D-20).

**Record decisions, don't just make them.** A new decision gets an ID, a one-line rationale, and a revisit trigger, in
the §14 table. This is what makes reversal a deliberate act rather than drift.

**Apply a design doc's impact list when you accept it.** The existing design doc has an "Impact on spec.md" section
that went unapplied for months, leaving two sources of truth silently diverged. That is the single most expensive
documentation failure this repo has had — do not repeat it.

**Keep Linear and the spec in sync.** When a decision lands in §14, comment on the affected Linear issues saying which
decision resolved them. When a spec section is amended, say so in the issue that prompted it.

**Markdown mechanics:** wrap prose at ~120 columns to match existing files. Tables must have consistent column counts —
this validates them:

```bash
awk 'BEGIN{t=0} /^\|/{n=gsub(/\|/,"|"); if(!t){t=1;h=n} else if(n!=h) print "COLMISMATCH line " NR; next} {t=0}' spec.md
```

## Verify, don't assert

This problem space has changed repeatedly and much of the research **postdates mid-2026**. Several load-bearing claims
are dated July–August 2026: the removal of the web client's `.rdpw` download, Microsoft's Q&A answer that no public API
exists, FreeRDP 3.30.0's contents, the first-party AVD client ID and feed scope, `arm.c`'s signature handling.

When these come up, mark them as needing verification rather than stating them as current fact. `spec.md` §12 risk 14
lists them; re-verification is the first task of Stage 0. The same applies to FreeRDP flag names (§5.5) — verify
against `xfreerdp --help` at 3.30 rather than from memory.

## What is actually open

- **Three verifications** that can *overturn* recorded decisions: `msal-extensions` locking on Linux (V1, can fire
  D-17's trigger), Flatpak portal/proxy/X11-socket exposure (V2, can fire D-3's), NFR-1/NFR-4 measured against
  PyGObject (V3, can fire D-16's).
- **Deferred with triggers:** live-session behavior when the client quits (decide at Stage 3); connection-config
  staticness (decide from the Stage 1 test).
- **The P1/P2 backlog** in `gapsandrecommendations.md` — notably the Cloud PC status × action matrix (G-19), session
  lifecycle (G-18), and the FreeRDP exit-code taxonomy (G-44), which can reopen D-1.

## Honesty obligations specific to this project

The project has two structural risks that are easy to soften accidentally, and both are recorded deliberately:

- **The addressable market may be much smaller than it looks.** Tenants enforcing device-based Conditional Access
  cannot use this client until broker integration lands, and those are disproportionately the tenants running
  Windows 365 (§12 risk 10). Do not let "the test tenant works" read as "tenants work."
- **The critical path depends on an undocumented endpoint and a capture method that may be blocked** (§12 risk 4/4a).
  A negative result is a legitimate outcome that D-14 pre-commits to — web-only MVP — not a problem to engineer
  around.

When summarizing status, say which things are *verified*, which are *decided but unverified*, and which are
*assumed*. Those three are very different here, and the distinction is most of the value this repository carries.
