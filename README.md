# WinOnLinux

A Linux desktop client for **Windows 365 Cloud PCs** and **Azure Virtual Desktop** — analogous to Microsoft's
Windows App, which is not available for Linux.

> **Status:** specification complete, implementation not started. No application code exists yet. The next step is a
> **5-working-day feasibility sprint** whose first three days answer the question the whole project depends on.

## Planned capabilities

- Entra ID sign-in with multiple accounts and account switching
- Unified enumeration of the user's Cloud PCs (Microsoft Graph) and AVD desktops/RemoteApps (AVD workspace feed)
- Per-resource choice of connection method: native (FreeRDP) or web (direct-launch URLs)
- Cloud PC management actions via Graph: restart, rename, troubleshoot, reset/reprovision
- Resilient token lifecycle: silent refresh, re-auth prompts, per-account keyring-backed token cache

## Why this is not `xfreerdp /v:host`

Windows 365 and AVD expose no inbound RDP listener. Connectivity uses **Reverse Connect**: the client authenticates
with Entra ID, obtains a connection configuration, and connects outbound to an AVD gateway over TCP 443; the broker
then has the session host connect back to the same gateway, and only then does the RDP handshake happen inside a
nested TLS transport.

**FreeRDP 3.30.0+ already implements everything below the connection configuration** — Entra token acquisition, ARM
gateway negotiation, reverse connect, the RDP session. The remaining gap is one call chain: the connection-critical
`.rdpw` comes only from the **undocumented AVD feed-discovery endpoint**, which FreeRDP never calls. FreeRDP starts
*after* you already hold that file.

So the work is a small **Connection-Config Provider** sitting above FreeRDP — feed discovery → parse → compose a
`.rdpw` → hand it to `xfreerdp`. Not a gateway, broker, or RDP reimplementation. The eventual goal is to upstream feed
support into FreeRDP so the capability is shared and maintained.

## Stack

Decided, each with a revisit trigger recorded in [spec.md](spec.md) §14:

| | |
| --- | --- |
| Language / UI | Python 3 + GTK4/libadwaita (PyGObject) |
| Auth | MSAL Python + `msal-extensions`; OS keyring required, no file fallback |
| RDP | `xfreerdp` ≥ 3.30.0, launched as a **subprocess** (not linked) |
| Packaging | Flatpak primary (it bundles FreeRDP, which distributions ship too old) |
| Concurrency | Single asyncio loop, per-account task groups |

## Roadmap

Gate-first: each step is cheap to abandon and answers one question before the next begins.

| Phase | What | Ships |
| --- | --- | --- |
| **0** — Web-first client | Sign-in, enumeration, direct-launch URLs, Graph actions | Yes |
| **1** — Native MVP (TCP-only) | Stages 0–3 below | Yes |
| **1.5** — Upstream | Contribute feed/`.rdpw` acquisition to FreeRDP | — |
| **2** — UDP Shortpath | Client-side `[MS-RDPEMT]`/`[MS-RDPEUDP2]` in FreeRDP | Optional |

Phase 1's stages:

1. **Stage 0** — app-identity feasibility spike: can our own Entra registration get an AVD feed token? **Timeboxed to
   3 days**; on expiry the project commits to a web-only MVP rather than extending the spike.
2. **Stage 1** — feed schema capture: pin down the undocumented feed contract.
3. **Stage 2** — Connection-Config Provider: token → feed discovery → generated `.rdpw`.
4. **Stage 3** — FreeRDP handoff: native session end to end, TCP-only.

Broker-based support for device-Conditional-Access tenants is committed and scheduled after Phase 1.

## Known limitations — read before evaluating

These are deliberate, recorded decisions rather than gaps to be discovered later:

- **Tenant admin consent is mandatory.** Both Cloud PC Graph scopes are delegated-and-admin-consent-only. The app is
  unusable in a tenant until an admin approves. Personal Microsoft accounts are not supported at all.
- **Tenants enforcing device-based Conditional Access are not supported** until broker integration lands. An
  unbrokered client on an unregistered Linux desktop cannot satisfy *require compliant device* or similar. The app
  detects this and says so rather than failing obscurely.
- **Native sessions are TCP-only.** FreeRDP has no client-side UDP Shortpath, so performance on lossy or
  high-latency links is worse than Microsoft's own clients until Phase 2.
- **Wayland runs through XWayland**, with documented limitations on keyboard grab, global shortcuts, cursor
  confinement, clipboard and per-monitor DPI. `xfreerdp` is an X11 client; there is no native-Wayland path.
- **Windows 365 Enterprise only** for Phases 0–1. Frontline, Business and Dev Box are out of scope.
- **Single monitor** in Phase 1. Multi-monitor span is deferred.
- **No telemetry.** Diagnostics are opt-in, user-triggered bundles.

## Documentation

- **[CTOOverview/overview.md](CTOOverview/overview.md)** — **new to the project? start here.** A plain-language
  introduction for technical and non-technical stakeholders: what we are building, why the gap exists, and what the
  five-day sprint does and does not buy.
- **[spec.md](spec.md)** — the full product and architecture specification, and the authoritative source. §14 is a
  **decision register**: 20 decisions, each with rationale and a revisit trigger.
- **[projectmanagement/projectplan.md](projectmanagement/projectplan.md)** — the 5-working-day feasibility sprint:
  entry conditions, the day-by-day plan, the gates, and the branch point at the end of day 3.
- **[gapsandrecommendations.md](gapsandrecommendations.md)** — assessment of specification completeness and
  feasibility: 54 findings, what was applied, and the outstanding backlog.
- **[Native connectivity design](docs/superpowers/specs/2026-08-18-windows365-native-connectivity-design.md)** — the
  original design for the native path. Partly superseded by `spec.md`; see the assessment for where it trails.

## A note on dates

Several load-bearing facts in the specification are dated July–August 2026 — the removal of the web client's `.rdpw`
download, Microsoft's confirmation that no public API exists, FreeRDP's handling of the `.rdpw` signature. These are
**re-verified as the first task of Stage 0**, because the plan's shape depends on them and this problem space has
changed repeatedly.
