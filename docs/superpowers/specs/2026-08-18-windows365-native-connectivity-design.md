# Windows 365 / AVD Native Connectivity on Linux — Design

**Date:** 2026-08-18
**Status:** Approved design; ready for implementation planning
**Related:** `spec.md.txt` (§5.2 native path, §8 the `.rdpw` gap, §11 roadmap Phase 1)

---

## Context

WinOnLinux ships a supported **web** connection path (Microsoft direct-launch URLs, plus the Graph beta
`retrieveCloudPcLaunchDetail` launch URL) and wants to add a **native** RDP path via FreeRDP so users get a real
desktop client instead of a browser tab.

Research (August 2026) established that native connectivity is far closer than the original spec implied,
because **FreeRDP 3.30.0+ already implements everything below the connection configuration**:

- It hardcodes the AVD public client ID (`a85cf173-…`) and scope (`https://www.wvd.microsoft.com/.default`),
  acquires the Entra token headlessly, and does the full live gateway negotiation itself
  (`POST /api/arm/v2/connections` in `libfreerdp/core/gateway/arm.c`, parsing `redirectedAuthGuid`,
  `gatewayLocation`, `redirectedServerName`).
- The `.rdpw` it consumes is **not** a live credential — it is mostly *static per-resource routing metadata*
  (`gatewayhostname`, `loadbalanceinfo`, `armpath`, `geo`, `workspace id`). FreeRDP **ignores the `.rdpw`
  signature**, so a reconstructed file needs no re-signing.

**The entire remaining gap is one step: programmatically obtaining that static routing blob.** Today it is
manual-download-only, and Microsoft has removed the web-client download (FreeRDP issue #13094). FreeRDP has no
feed-discovery step; it starts *after* you already hold the `.rdpw`.

**Microsoft Graph cannot fill this gap.** Graph supplies enumeration, management actions, and a
`cloudPcLaunchUrl` — but that URL is an HTML *web-launch* bootstrap (`/api/arm/weblaunch/...`), not
machine-readable connection settings. The connection-critical fields exist **only** in the AVD feed
(`https://rdweb.wvd.microsoft.com/api/arm/feeddiscovery`), whose request/response schema Microsoft does not
publicly document. Graph's role is therefore a *discovery aid* (it yields the regional `rdweb` host and the
tenant/resource GUIDs), not the connection config.

### Decisions taken (this design)

- **Target:** a **distributable product**, not a personal tool. This favors upstreaming feed support into
  FreeRDP over a private, fragile shim.
- **App identity:** prefer **our own Entra app registration**; **reuse the first-party AVD client ID
  (`a85cf173-…`) only if** our own registration cannot obtain a usable AVD feed token.
- The whole effort is **feasibility-gated on the app-identity question** — it is answered first and cheaply
  before any product code is written.

### Intended outcome

A native FreeRDP connection to a Windows 365 Cloud PC (and AVD resource) from Linux with **no manual `.rdpw`
handling**, using a supportable identity model, with the web client remaining the always-available fallback.

---

## Goals / non-goals

**Goals**
- Prove and then productize programmatic acquisition of the per-resource connection configuration.
- Keep the connection-config provider a small, well-bounded unit with a documented interface.
- Land the capability in FreeRDP upstream so it is shared and maintained.

**Non-goals (this design)**
- UDP Shortpath / multitransport — remains a separate later project ([MS-RDPEMT]/[MS-RDPEUDP2]); native stays
  TCP-only.
- Replacing the web fallback — it ships first and stays.
- Admin/provisioning functionality.

---

## Architecture

The native path adds one new component — the **Connection-Config Provider** — between the existing Auth/Account
Manager and the FreeRDP launcher. Everything else already exists in the product or in FreeRDP.

```text
Auth / Account Manager ──token──► Connection-Config Provider ──.rdpw──► FreeRDP launcher ──► Cloud PC / AVD
   (Entra, per-account cache)         │  feed discovery                  (arm gateway, /sec:aad)
                                       │  parse feed → serialize .rdpw
   Graph (enumeration, actions,        ▼
   regional host + resource GUIDs) ──► targeting inputs
```

**Connection-Config Provider** — the only genuinely new logic. Given an account token and a target resource
(Cloud PC id / AVD resource), it:
1. resolves the correct regional feed endpoint (aided by Graph `retrieveCloudPcLaunchDetail`),
2. calls AVD feed discovery with a bearer token for the AVD feed audience,
3. parses the returned workspace/resource feed,
4. serializes a per-resource `.rdpw` (no signature required for FreeRDP).

Interface (conceptual): `getConnectionConfig(account, resourceRef) -> RdpwFile | Unavailable(reason)`.
Consumers (the native launcher) never see feed internals; if the provider returns `Unavailable`, the UI falls
back to the web path with a reason (per `spec.md.txt` §4.2 disabled-state rules).

---

## Staged, gate-first plan

### Stage 0 — App-identity feasibility spike (the gate)

**Purpose:** answer, before building anything, whether our **own** Entra public client can obtain a usable AVD
feed token.

**Work:** register an Entra **public client** with a native/loopback redirect (explicitly **not** `spa` — an
`spa` redirect caps refresh tokens at 24h). Attempt to acquire a token for the AVD feed resource
(`https://www.wvd.microsoft.com/.default` or an equivalent delegated permission on the AVD API `9cdead84-…`)
and call `GET https://rdweb.wvd.microsoft.com/api/arm/feeddiscovery`.

**Exit criteria (go/no-go):**
- **Own registration works** → proceed to Stage 1 with our own app identity.
- **Scope not grantable** → **fall back to reusing the first-party AVD client ID (`a85cf173-…`)** as FreeRDP
  does (decision already taken). Proceed to Stage 1 with that identity, and treat first-party reuse as an
  explicit, documented product risk to revisit at Stage 4 (upstreaming makes it defensible).

**Output:** a one-page finding: which identity works, the exact scope/consent flow, and any admin-consent
requirement. No product code.

### Stage 1 — Feed schema capture

**Purpose:** pin down the undocumented feed contract once.

**Work:** with a working token, capture the exact `feeddiscovery` → workspace-feed-download request/response
(cross-check against the official Windows App / web client via a proxy such as mitmproxy). Confirm the feed
response already carries the full `.rdpw` field set: `gatewayhostname`, `loadbalanceinfo`, `armpath`, `geo`,
`workspace id`, `full address`/`alternate full address`, `remoteapplicationprogram`.

**Exit criteria:** a documented request/response schema and a field-mapping table (feed field → `.rdpw`
property). Expected result: the feed carries everything, since the web client rendered its old download from it.

### Stage 2 — Connection-Config Provider (product code)

**Purpose:** the productized provider described in Architecture.

**Work:** implement `getConnectionConfig`: token acquisition (Stage 0 identity) → feed discovery → parse →
serialize `.rdpw`. Handle the "VM deallocated / starting" retry case FreeRDP already models
(`E_PROXY_ORCHESTRATION_LB_SESSIONHOST_DEALLOCATED`). Cache the *static* routing blob per resource; re-fetch on
demand and on feed/auth errors. Return `Unavailable(reason)` on any failure so the UI can fall back to web.

**Exit criteria:** given an account and a resource, the provider emits a `.rdpw` FreeRDP accepts, end to end,
with no manual file handling.

### Stage 3 — FreeRDP handoff

**Purpose:** connect using the generated config.

**Work:** invoke FreeRDP 3.30.0+ with the generated `.rdpw` and `/gateway:type:arm /sec:aad`; manage the
session window and surface gateway/session errors (per `spec.md.txt` §9). This half is already proven upstream;
no new protocol work.

**Exit criteria:** a native Cloud PC session established on Linux from a cold start (sign in → enumerate →
Connect (Native)), TCP-only.

### Stage 4 — Upstream to FreeRDP (product-grade finish)

**Purpose:** make the capability shared, maintained, and ToS-defensible.

**Work:** contribute the feed/`.rdpw` acquisition to FreeRDP so it grows a first-class
`/feed:<url> /sec:aad`-style mode with no manual `.rdpw`. If Stage 0 forced first-party-ID reuse, upstreaming is
the correct home for that behavior (a community client feature rather than a closed app scripting undocumented
endpoints).

**Exit criteria:** an upstream PR (or accepted feature) that lets FreeRDP subscribe + connect without an
out-of-band `.rdpw`; the product depends on that path rather than a private shim.

---

## Unverified assumptions to resolve empirically (front-loaded)

1. **(Stage 0)** Whether a *custom* app registration can obtain the AVD feed scope, and whether it needs admin
   consent. — Gates the identity model; fallback is first-party-ID reuse.
2. **(Stage 1)** The exact `api/arm/feeddiscovery` request/response under the ARM/AAD model.
3. **(Stage 1)** That the feed response contains the complete `.rdpw` field set (expected: yes).
4. **(Stage 3)** That `weblaunch` is irrelevant to the native path (it is an HTML bootstrap) — confirmed by
   research; native goes through feed discovery, not `weblaunch`.

---

## Risks

- **Identity may not be grantable to a custom app** (Stage 0). Mitigation: documented fallback to first-party
  client-ID reuse; upstreaming (Stage 4) makes that posture defensible.
- **Undocumented feed protocol may change.** Mitigation: isolate all feed knowledge in the Connection-Config
  Provider behind `getConnectionConfig`; the web fallback always remains; upstreaming shares the maintenance
  burden.
- **ToS/support risk** from calling undocumented endpoints or reusing a first-party client ID. Mitigation:
  prefer own registration; land the behavior in FreeRDP upstream; keep the supported web path as default.
- **TCP-only performance** on lossy/high-latency links until Shortpath (out of scope here).
- **Beta Graph dependency** for `retrieveCloudPcLaunchDetail` targeting aid (`getCloudPcLaunchInfo`
  deprecated 2026-10-30). Mitigation: the provider can derive the regional host from feed discovery itself; the
  Graph launch URL is an optimization, not a hard dependency.

---

## Verification

- **Stage 0:** demonstrate a successful `feeddiscovery` HTTP 200 with a token from the chosen identity; record
  which identity and consent path worked.
- **Stage 1:** a saved request/response capture and a field-mapping table checked into the design/spec.
- **Stage 2:** unit-test the parser against the captured feed fixture; assert the serialized `.rdpw` contains
  every field FreeRDP's `arm.c` reads (`gatewayhostname`, `loadbalanceinfo`, `armpath`, `geo`,
  `remoteapplicationprogram`, `full address`).
- **Stage 3:** end-to-end — a real native Cloud PC session from a cold start, verified by observing the
  session window and the `POST /api/arm/v2/connections` succeeding (FreeRDP TRACE).
- **Stage 4:** the upstream path reproduces Stage 3 without a manually supplied `.rdpw`.

---

## Impact on `spec.md.txt`

On acceptance, update the main spec: §8 (the gap is a single feed-discovery step, not a Reverse-Connect
reimplementation — FreeRDP already does the gateway), §5.2 (Connection-Config Provider component), and §11
Phase 1 (replace "reverse-map the feed" with this staged, gate-first plan; note first-party-ID fallback and the
Stage 4 upstreaming goal).
