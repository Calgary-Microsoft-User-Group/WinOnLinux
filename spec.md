# WinOnLinux — Windows 365 / Azure Virtual Desktop Client for Linux

## Specification

---

## 1. Introduction and scope

WinOnLinux is a Linux desktop client for **Windows 365 Cloud PCs** and **Azure Virtual Desktop (AVD)
desktops and RemoteApps** — functionally analogous to Microsoft's "Windows App" on other platforms, which
Microsoft does not ship for Linux.

The application provides:

- Entra ID sign-in with support for **multiple simultaneously signed-in accounts** and fast account switching.
- A main resource area that **enumerates the signed-in user's resources** from both providers: Windows 365
  Cloud PCs (via Microsoft Graph) and AVD desktops/RemoteApps (via the AVD workspace feed).
- A **per-resource choice of connection method**: native (FreeRDP) or web-based (browser + Microsoft web client).
- **Cloud PC management controls** surfaced through Microsoft Graph: restart, rename, troubleshoot, and
  reset/reprovision.
- Robust handling of **token expiration**: silent refresh, re-authentication prompts, and per-account token
  caches.

In scope: an end-user client. Out of scope: admin-console functionality (provisioning policies, tenant
management), although actions that require admin capability are noted where they intersect the end-user UI.

Sections 1–10 are written at the **protocol and behavior level** rather than against an implementation stack, and
authentication in particular is specified as OAuth 2.0 rather than as library calls — so those sections stay valid if
the stack changes. The stack itself **is** decided, because the rest of the design depends on it: **Python 3 +
GTK4/libadwaita** (D-16), **MSAL Python** (D-17), an **asyncio concurrency model** (D-18), a **FreeRDP subprocess**
(D-1, section 5.1.1) and **Flatpak packaging** (D-3, section 5.8). Every one carries a revisit trigger in the
decision register (section 14).

Functional requirements are numbered **FR-1 … FR-5** (section 3), each with numbered acceptance criteria
(`FR-n-AC-m`) that define done. The delivery roadmap (section 11) maps every requirement and criterion onto
phases, and section 13 specifies how each is verified.

---

## 2. Background and constraints

### 2.1 Why this is not `xfreerdp /v:host`

Windows 365 and AVD do **not** expose a traditional inbound RDP listener on `IP:3389`. Connectivity uses
**Reverse Connect**: the client authenticates with Entra ID, obtains a digitally signed connection
configuration, and connects outbound to an AVD gateway over TCP 443; the broker arranges for the Cloud
PC/session host to connect back to the same gateway, and only then does the actual RDP handshake take place
inside a nested TLS transport. ([Microsoft Learn][1])

```text
Linux
  │
  │ Entra ID authentication / resource discovery
  ▼
Windows 365 / AVD control plane
  │
  │ signed connection configuration
  ▼
AVD Gateway / Broker
  │
  ├── TCP 443  Reverse Connect   ← always establishes first
  │
  ├── UDP 3478 STUN             ← attempts direct Shortpath
  │
  └── UDP 3478 TURN             ← relayed Shortpath fallback
  │
  ▼
Windows 365 Cloud PC / AVD session host
       RDP session
```

**RDP Shortpath** does not remove the bootstrap requirement: the session first establishes the TCP/443 Reverse
Connect transport, then exchanges STUN candidates and may move the RDP channels onto direct UDP, falling back to
TURN relay if direct connectivity fails. For ANC/private-network Cloud PCs there is also private Shortpath over
UDP/3390, but the public Reverse Connect path is still required initially. The TCP channel remains available
whenever UDP cannot be established. ([Microsoft Learn][1])

Microsoft's architecture description of the client flow: after Entra authentication, the client sends its token
to the AVD feed service, receives **digitally signed connection configurations**, stores them as `.rdp` files,
and uses one of those configurations to establish the gateway connection. The broker then causes the session
host to connect to the same gateway, after which the nested RDP/TLS session starts. ([Microsoft Learn][a2])

### 2.2 What is already solved (FreeRDP)

Modern FreeRDP implements enough of the AVD/Windows 365 ARM-gateway path to authenticate with Entra ID, resolve
the gateway, traverse the WebSocket/TCP 443 gateway, and establish an RDP session. Users demonstrably run this
against Windows 365 Cloud PCs, and upstream has continued fixing gateway compatibility through 2026.
([GitHub][a1]) The upstream FAQ documents the invocation pattern:

```text
<rdpw file> /gateway:type:arm /sec:aad
```

([GitHub][a3])

Build against **FreeRDP 3.30.0 or newer** (released July 16, 2026, including WebSocket fixes), not whatever
older version a distribution ships. ([GitHub][a4])

| Piece                                  | Status                                                  | Consequence                            |
| -------------------------------------- | ------------------------------------------------------- | -------------------------------------- |
| Entra authentication                   | **Implemented in FreeRDP**                              | Don't write this yourself              |
| AVD ARM/gateway resolution             | **Implemented in FreeRDP**                              | Reverse-connect bootstrap is viable    |
| TCP 443 gateway/WebSocket              | **Implemented**                                         | Native Windows 365 sessions work today |
| RDP/RDSTLS                             | **Implemented + protocol documented**                   | Normal FreeRDP territory               |
| Cloud PC enumeration                   | **Public Graph API**                                    | Easy application UI/discovery          |
| Direct browser launch                  | **Public/supported API surface**                        | Excellent fallback                     |
| Obtain connection config (`.rdpw`) programmatically | **Weak spot — the one remaining gap**      | Main native-client integration problem |
| Shortpath/UDP                          | **Protocol documented, FreeRDP missing implementation** | Native client stays on TCP gateway     |

**FreeRDP does not verify the `.rdpw` signature.** It reads the file as per-resource routing metadata
(`gatewayhostname`, `loadbalanceinfo`, `armpath`, `geo`, `full address`, `remoteapplicationprogram`) and performs
the live Entra authentication and gateway negotiation itself. A connection configuration **composed by this
client** from feed data is therefore accepted without re-signing. That is what makes the native path tractable —
and it moves the integrity burden from Microsoft's signature onto this client, which section 10.1 specifies.

The two remaining gaps — programmatic acquisition of the connection configuration (section 8) and
client-side UDP Shortpath in FreeRDP (section 11, Phase 2) — shape the roadmap. Do **not** spend engineering
effort reimplementing the gateway, broker, RDSTLS, or reverse-connect RDP stack; FreeRDP has crossed that
bridge. The code to own is the **session-acquisition/control-plane layer immediately above FreeRDP** plus the
application described in this document.

---

## 3. Functional requirements

### FR-1 — Sign-in and resource enumeration

The main area of the application lets the user sign into an Entra ID account and presents a unified list of that
user's remote resources from **both** providers:

- **Windows 365 Cloud PCs**, enumerated via Microsoft Graph `GET /me/cloudPCs` (v1.0, delegated
  `CloudPC.Read.All`). Each entry carries display name, Cloud PC `id`, status, and provisioning metadata.
- **AVD desktops and RemoteApps**, enumerated via the AVD workspace feed (feed discovery → workspace feed
  download). Graph **cannot** enumerate AVD resources for an end user; AVD objects live under ARM
  (`Microsoft.DesktopVirtualization`) and require Azure RBAC that ordinary end users lack. See section 8.

The list refreshes on sign-in, on account switch, on manual refresh, and periodically while the app is
foregrounded (section 7.3).

**Acceptance criteria**

- **FR-1-AC-1** — For a consented account with ≥ 1 provisioned Cloud PC, sign-in followed by enumeration lists
  every Cloud PC returned by `GET /me/cloudPCs`, **including every page** of a paged response, within the
  NFR-2 latency budget (section 5.7).
- **FR-1-AC-2** — Each Cloud PC entry displays display name and status; the Graph `id` is retained and is the
  identifier subsequently used for FR-2 web launch and all FR-5 actions (no second lookup).
- **FR-1-AC-3** — An account with a Cloud PC licence but nothing provisioned shows the empty state; an account
  with no licence (Graph `404`) shows the **distinct** no-licence empty state. Neither renders as an error.
- **FR-1-AC-4** — A refresh that fails leaves the previously enumerated list visible with an error indicator; it
  never clears the list.
- **FR-1-AC-5** — Phase 0: AVD entries appear only for admin-provisioned workspace/resource IDs. Phase 1 (Stage 2
  complete): AVD workspaces, desktops and RemoteApps assigned to the account are enumerated from the feed and
  grouped by workspace, with no admin pre-provisioning.

### FR-2 — Per-resource connection method (Native vs Web)

Every resource offers two launch methods:

- **Connect (Native)** — FreeRDP session over the ARM gateway path, launched as a subprocess (sections 5.1.1, 5.2).
- **Connect (Web)** — system browser launched at the Microsoft web client via a direct-launch URL
  (section 5.3).

The user's last-used method is remembered **per resource** and becomes that resource's default. A method that is
currently unavailable is shown disabled with a reason (e.g., "Native mode unavailable: connection configuration
could not be acquired" during Phase 0, or "Web launch unavailable: workspace/resource ID unknown" for AVD entries
without admin-provided IDs).

**Acceptance criteria**

- **FR-2-AC-1** — Every resource entry exposes both methods. An unavailable method is disabled, never hidden, and
  its tooltip states the specific reason.
- **FR-2-AC-2** — The last-used method for a resource persists across an app restart and is the primary action of
  that resource's split button on next launch.
- **FR-2-AC-3** — Web launch opens the system browser at a URL built per section 5.3, with `?tenant=` before the
  fragment and `#loginHint=<active UPN>` as the final component; the browser lands on the active account without
  an account picker.
- **FR-2-AC-4** — Phase 1: Native launch on a Cloud PC in a connectable state (section 7.4) produces a visible
  RDP session window **from a cold start, with no manual file handling**, within the NFR-3 budget, TCP-only.
- **FR-2-AC-5** — A native launch failure presents web fallback in the same failure surface, in one action.
- **FR-2-AC-6** — A connection configuration failing the section 10.1 field validation is rejected and reported as
  a **security** error; no session is attempted.

### FR-3 — Multiple accounts and account switching

- The user can sign in additional accounts at any time ("Add account") without signing out existing ones.
- Exactly one account is **active** at a time. The resource list, all Graph calls, all feed queries, all
  management actions, and all connection launches are scoped to the active account.
- Switching accounts swaps the resource list and action context; it does not disturb the other accounts' cached
  tokens or live sessions.
- Each account can be signed out individually; signing out removes that account's tokens from the cache and its
  resources from the UI.
- Account state is isolated per account: token cache entries, per-resource connection preferences, and
  auth-state (section 6.4) are all keyed by account.
- Web launches carry the active account's UPN as `#loginHint=` so the browser-side web client lands on the same
  account (section 5.3).

**Acceptance criteria**

- **FR-3-AC-1** — Two or more accounts are signed in simultaneously, each with its own cache entry; adding an
  account does not invalidate, re-prompt, or disturb any existing one.
- **FR-3-AC-2** — Exactly one account is active. Switching swaps the resource list within the NFR-2 budget and
  **cancels in-flight enumeration and pending actions** belonging to the previous account.
- **FR-3-AC-3** — Signing out account A removes A's tokens from the cache and A's resources from the UI; account B
  continues to enumerate with no re-authentication.
- **FR-3-AC-4** — Accounts are keyed by **home account ID**, so the same person in two tenants appears as two
  independent accounts with independent state.
- **FR-3-AC-5** — An account in `ReauthRequired` shows its badge and banner while every other account stays fully
  functional, including live sessions.

### FR-4 — Token expiration handling

The application **expects** token expiration as a normal condition, not an error:

- Access tokens (lifetime ~60–90 minutes) are acquired **silently** from the per-account cache/refresh-token
  flow immediately before every Graph call, feed query, and connection launch. The app never schedules work off
  wall-clock lifetime assumptions.
- When silent acquisition fails (refresh token expired after 90 days of inactivity, revocation, password
  change/`invalid_grant`), the account enters a **ReauthRequired** state: a non-blocking banner on that account
  prompts interactive re-authentication (with `login_hint` prefilled). Other accounts are unaffected.
- Conditional Access / Continuous Access Evaluation claims challenges (`interaction_required`, `claims`) trigger
  interactive re-auth carrying the returned `claims` parameter.
- The full state machine is specified in section 6.4. Conditional Access policies that require a device state this
  client cannot satisfy are a **different** condition and are specified separately in section 6.5.

**Acceptance criteria**

- **FR-4-AC-1** — Every outbound Graph call, feed query, and connection launch is immediately preceded by a silent
  acquisition for the owning account. No code path derives refresh timing from an assumed token lifetime.
- **FR-4-AC-2** — An expired access token with a valid refresh token refreshes with **zero** user-visible
  interaction, and the rotated refresh token is persisted before the call proceeds.
- **FR-4-AC-3** — `invalid_grant` moves only that account to `ReauthRequired`; its banner's inline button starts
  interactive auth with `login_hint` prefilled.
- **FR-4-AC-4** — A claims challenge produces an interactive request carrying the returned `claims` **verbatim**.
  A test asserts the app never issues an unmodified retry of a claims-challenged request, and that retry attempts
  are bounded.
- **FR-4-AC-5** — Network failure during silent refresh yields the **Offline** state, not `ReauthRequired`, and an
  unexpired cached access token continues to be used.
- **FR-4-AC-6** — Concurrent silent acquisitions for one account produce **exactly one** network refresh, and the
  rotated refresh token is written once. A test issues N simultaneous acquisitions against an expired access token
  and asserts one refresh call and one surviving valid refresh token (section 6.3).
- **FR-4-AC-7** — A second application launch activates the running instance rather than starting a competing one,
  so two processes never share the token cache (section 6.3).

### FR-5 — Cloud PC management actions (Graph)

For each Cloud PC, the app exposes management controls via Microsoft Graph:

- **Restart** (`reboot`), **Rename**, **Troubleshoot** — self-service via `POST /me/cloudPCs/{id}/...`
  (Graph **beta**; see section 7.2).
- **Reset/Reprovision** (`reprovision`) — self-service via beta `/me` path; **destructive** (wipes the Cloud
  PC back to a fresh image), so the UI requires an explicit typed/checked confirmation before invoking it.
- **Restore to snapshot** and **Resize** — admin-context only (`/deviceManagement/virtualEndpoint/...`); shown
  only when the active account demonstrably has admin capability, otherwise hidden.

All actions require delegated `CloudPC.ReadWrite.All` (admin consent; section 6.2). Action invocations show
progress and surface the resulting status transition in the resource list. Which actions are offered in which
Cloud PC state is governed by the enablement matrix in section 7.4.

**Acceptance criteria**

- **FR-5-AC-1** — Restart, Rename and Troubleshoot, invoked on a Cloud PC in a permitting state, each issue the
  documented call, show progress, and surface the outcome; the entry's status chip reflects the transition on the
  next refresh.
- **FR-5-AC-2** — Reprovision requires explicit typed or checked confirmation. Cancelling issues **no** call. The
  call is never batched and never automatically retried.
- **FR-5-AC-3** — Restore and Resize are hidden for accounts not determined admin-capable; capability-unknown is
  treated as not-capable. The UI never shows an action that then fails on authorization.
- **FR-5-AC-4** — An action rejected for missing consent surfaces the guided consent flow (section 9), not a
  generic error.
- **FR-5-AC-5** — A beta contract error disables only the affected action with the "Microsoft API change" message
  and logs for triage; enumeration and both connection methods keep working.
- **FR-5-AC-6** — In a national cloud where Cloud PC Graph APIs are unavailable, FR-5 is disabled wholesale with
  an explanatory message rather than failing per action.

---

## 4. User interface specification (toolkit-agnostic)

### 4.1 Main window

```text
┌────────────────────────────────────────────────────────────┐
│  [● user@contoso.com ▾]                        [⟳ Refresh] │
│    ├─ user@contoso.com        (active)                     │
│    ├─ admin@fabrikam.com      ⚠ reauthentication required  │
│    ├─ ──────────────                                       │
│    ├─ Add account…                                         │
│    └─ Sign out user@contoso.com                            │
├────────────────────────────────────────────────────────────┤
│  Windows 365                                               │
│   ┌──────────────────────────────────────────────────────┐ │
│   │ 🖥  Cloud PC — Standard 4vCPU   ● Running             │ │
│   │     [Connect ▾]  [⋮ Actions ▾]                       │ │
│   │        ├─ Connect (Native)        ├─ Restart          │ │
│   │        └─ Connect (Web browser)   ├─ Rename           │ │
│   │                                   ├─ Troubleshoot     │ │
│   │                                   └─ Reset (Reprovision)│
│   └──────────────────────────────────────────────────────┘ │
│  Azure Virtual Desktop                                     │
│   Workspace: Contoso Finance                               │
│   ┌──────────────────────────────────────────────────────┐ │
│   │ 🖥  Finance Desktop            [Connect ▾]            │ │
│   │ 📦  Excel (RemoteApp)          [Connect ▾]            │ │
│   └──────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

- **Account switcher** (top-left): dropdown listing every signed-in account with avatar/UPN, marking the active
  one, plus a per-account auth-state badge ("reauthentication required" when in ReauthRequired state).
  Menu items: switch (click an account), "Add account…", "Sign out <account>".
- **Resource list**: grouped by provider ("Windows 365", "Azure Virtual Desktop"), AVD further grouped by
  workspace. Each entry shows name, type (Cloud PC / desktop / RemoteApp), and live status where available
  (Cloud PC `status` from Graph).

### 4.2 Connect controls

Per-resource split button or menu: the primary action is the resource's remembered default method; the dropdown
offers "Connect (Native)" and "Connect (Web browser)". Disabled entries carry tooltips stating the reason
(FR-2). Launch feedback: spinner on the entry until the native session window appears or the browser is
spawned; failures surface per section 9.

### 4.3 Cloud PC action menu

Actions menu per Cloud PC entry: Restart, Rename, Troubleshoot, Reset (Reprovision). Reprovision opens a
destructive-action dialog requiring explicit confirmation and explaining that the Cloud PC is wiped and rebuilt.
Restore and Resize appear only for admin-capable accounts. Each invoked action shows progress and a
success/failure toast; the entry's status chip updates on the next refresh.

### 4.4 UI states

- **Loading** — skeleton list while enumeration is in flight.
- **Empty** — signed in but no resources ("No Cloud PCs or AVD resources are assigned to this account"), with a
  distinct message for missing license (Graph 404; section 9).
- **Offline** — network unreachable: cached resource list shown greyed with an offline banner; connect actions
  disabled.
- **Error** — enumeration failed: inline error with retry.
- **ReauthRequired banner** — per-account, non-blocking: "Your sign-in for <UPN> has expired. Sign in again."
  with an inline button that starts interactive auth for that account only.
- **Consent-required** — first-run in an unconsented tenant: guided admin-consent screen (section 9).

---

## 5. Architecture

### 5.1 Component model

```text
                        ┌──────────────────────────────┐
                        │            UI shell           │
                        │  account switcher · resource  │
                        │  list · action menus · states │
                        └──────┬───────────────┬───────┘
                               │               │
              ┌────────────────┴──┐     ┌──────┴──────────────┐
              │  Auth / Account   │     │  Resource layer      │
              │  Manager          │     │                      │
              │  · OAuth2+PKCE    │     │  ├ Graph CloudPC     │
              │  · per-account    │◄────┤  │ provider (v1.0)   │
              │    token cache    │token│  ├ AVD Feed provider │
              │  · state machine  │     │  └ Management Action │
              │  (section 6)      │     │    service (Graph)   │
              └────────┬──────────┘     └──────┬───────────────┘
                       │                       │
                       │  token        ┌───────┴──────────────────┐
                       ├──────────────►│  Connection-Config       │
                       │               │  Provider  (section 5.2) │
                       │               │  · feed discovery        │
                       │               │  · feed → .rdpw compose  │
                       │               │  · field validation      │
                       │               └───────┬──────────────────┘
                       │                       │ .rdpw │ Unavailable(reason)
                       │              ┌────────┴──────────────────┐
                       │              │  Connection launchers      │
                       │              │  ├ Native: FreeRDP 3.30+   │
                       │              │  │  subprocess (5.1.1)     │
                       │              │  └ Web: browser +          │
                       │              │     direct-launch URL      │
                       │              └────────┬──────────────────┘
                       │                       │
            OS keyring (libsecret /            │
            KWallet) for token storage         ▼
                                     Windows 365 / AVD
```

- **Auth/Account Manager** — owns interactive and silent token acquisition, the per-account encrypted token
  cache, and the per-account auth state machine (section 6).
- **Graph CloudPC provider** — `GET /me/cloudPCs` enumeration and status refresh.
- **AVD Feed provider** — feed discovery + workspace feed download, supplying AVD **enumeration** (FR-1). In
  Phase 0 this provider is replaced by an admin-provisioned static resource list (section 11).
- **Connection-Config Provider** — the only genuinely new logic in the native path (section 5.2). Given an account
  token and a target resource it resolves the regional feed endpoint, calls feed discovery, parses the workspace
  feed, validates every field per section 10.1, and composes a `.rdpw`. Interface:
  `getConnectionConfig(account, resourceRef) -> RdpwFile | Unavailable(reason)`. Consumers never see feed
  internals; `Unavailable` drives the FR-2 disabled state and the web fallback.
- **Management Action service** — Graph Cloud PC actions (FR-5).
- **Native launcher** — launches **FreeRDP 3.30.0+ as a subprocess** (section 5.1.1) with the generated config and
  the upstream AVD flags (`<rdpw> /gateway:type:arm /sec:aad`); tracks the child process, maps its exit status to
  the section 9 error taxonomy, and enforces the argument-handling rules of section 10.4.
- **Web launcher** — builds direct-launch URLs (section 5.3) and opens the system browser.

#### 5.1.1 Decision record — FreeRDP integration mode

**Decision:** the native launcher invokes FreeRDP as a **subprocess**. It does not link `libfreerdp`.

| | Subprocess (`xfreerdp` / `sdl-freerdp`) | Linked `libfreerdp` |
| --- | --- | --- |
| Language freedom | Any | C/C++/Rust realistically |
| Session window | FreeRDP's own; not embeddable in our shell | Embeddable, unified UX |
| Error surfacing | Exit status + stderr (coarse) | Structured callbacks |
| Config handoff | File on disk, `0600`, deleted after start | In-memory possible |
| ABI/version risk | Low — CLI flags are the documented upstream contract | High — the 3.x API churns |
| Crash isolation | Session crash cannot take the client down | Shared address space |

**Rationale:** the CLI invocation is the surface upstream documents and supports, it keeps the section 11
language/toolkit gate genuinely open, and it isolates session crashes from the client. The cost accepted is a
non-embeddable session window and coarser error detail.

**Revisit if:** embedded or tabbed session windows become a requirement, or exit-status granularity proves
insufficient to satisfy the section 9 taxonomy. Both are Phase 1.5-or-later concerns.

**Consequences:** the config is always handed over as a file, never as command-line values (section 10.4); FreeRDP
must be present at a known version, which the packaging model resolves (section 5.8) and a runtime check enforces
(NFR-8).

### 5.2 Native connection path

```text
Linux client application
       │
       ├──────── Microsoft Graph ── enumerate Cloud PCs, CloudPc.Id, actions
       │
       ├──────── Connection-Config Provider
       │              └── connection configuration (.rdpw), composed from the feed
       │                      ▲
       │                 THE ONE GAP (section 8) — feed discovery is the missing call
       │
       └──────── FreeRDP 3.x (3.30.0+)
                      ├── Entra/AAD authentication
                      ├── AVD ARM gateway resolution
                      ├── gateway/broker connection
                      ├── WebSocket / TCP 443
                      ├── Reverse Connect
                      ├── RDSTLS / RDP
                      └── UDP Shortpath   ✗ not implemented in FreeRDP today
```

Everything below the `.rdpw` in this stack (AAD auth, ARM gateway, TCP/443 reverse connect, RDP/RDSTLS) is
already handled by FreeRDP. The application's native path therefore consists of: acquire token (section 6) →
obtain a validated `.rdpw` for the chosen resource from the Connection-Config Provider (section 8) → hand it to a
FreeRDP subprocess with the ARM-gateway/AAD options (section 5.1.1) → manage the session lifecycle.

The configuration is **composed by this client**, not downloaded pre-signed: FreeRDP does not verify the `.rdpw`
signature (section 2.2), so no re-signing is required and the integrity of every field is this client's
responsibility. Section 10.1 specifies the validation that replaces the signature as a trust control.

**Configuration caching.** Whether the routing fields are stable over time is an **unverified assumption**
(section 12) — Microsoft describes `loadbalanceinfo` as a routing *token*, and section 9 already assumes
configurations can expire. Until staticness is demonstrated at Stage 1, the provider therefore:

- **does not persist** configurations across app restarts, and keeps none on disk beyond the short-lived
  `0600` handoff file, which is deleted once the subprocess has started;
- caches in memory **per (account, resource)** for the lifetime of the app process only;
- re-fetches on the **first launch of each resource per app session**, then reuses the in-session cache for
  subsequent launches of that same resource (decision **D-5**, section 14). The first connect therefore pays full
  acquisition cost and stays fresh; later connects fit the NFR-3 budget without persisting anything to disk;
- invalidates on: account token change or re-auth, an observed resource state change, any feed or auth error, and
  any gateway rejection during connect.

Stage 1 must test staticness explicitly (re-fetch the same resource at T+0, +1 h and +24 h and diff the fields).
Only a passing result may relax these rules, and any relaxation must state a TTL.

Until Phase 2 (section 11), native sessions run TCP-only:

```text
Windows 365 + FreeRDP today
        └── Reverse Connect TCP/443   ✓
Windows 365 + FreeRDP Shortpath
        ├── STUN/direct UDP           ✗
        └── TURN/relayed UDP          ✗
```

**Alternative RDP engines (watch list).** **IronRDP** (Devolutions) is a maintained pure-Rust RDP
implementation with .NET/WASM bindings — full RDP core, CredSSP/NLA, TLS 1.3, and the RDCleanPath WSS-bridging
extension; it is used by Devolutions Gateway, Cloudflare Access, and Teleport. It has **no AVD ARM-gateway
support** today, so it is not a current option for this project, but it is the most credible future alternative
engine and worth monitoring. ([GitHub][n1]) FreeRDP remains the only open-source stack with working
AVD/Windows 365 gateway support. Microsoft's own legacy Remote Desktop clients are end-of-support
(September 2025; extended to September 2026 for Gov clouds) with documentation moved to "previous-versions" —
everything consolidates on Windows App, which has no Linux version and no public SDK. ([Microsoft Learn][n2])

The properties appearing in AVD `.rdp`/`.rdpw` files are documented in Microsoft's **Supported RDP
properties** reference — useful when validating or synthesizing connection files handed to FreeRDP.
([Microsoft Learn][n3])

### 5.3 Web connection path

Direct-launch URLs open the Microsoft web client straight into a session, skipping portal navigation.
([Microsoft Learn][a6])

- **Windows 365** (Enterprise and Flex Dedicated):

  ```text
  https://windows.cloud.microsoft/webclient/ent/<CloudPc.Id>
  https://windows.cloud.microsoft/webclient/ent/<CloudPc.Id>?tenant=<tenantID>#loginHint=<UPN>
  ```

  `<CloudPc.Id>` is exactly the `id` Graph returns for the Cloud PC — the same identifier used for FR-5
  actions, so the web path needs no extra discovery.

  Additionally, Graph beta exposes `GET /me/cloudPCs/{id}/retrieveCloudPcLaunchDetail` (delegated
  `CloudPC.Read.All`), which returns a per-Cloud-PC `cloudPcLaunchUrl` of the form
  `https://rdweb-r0.wvdselfhost.microsoft.com/api/arm/weblaunch/tenants/<tenantId>/resources/<resourceId>`.
  ([Microsoft Learn][r7]) The web launcher should prefer this Microsoft-issued URL when available (it is the
  supported per-resource connect URL, and it carries the rdweb-namespace tenant/resource identifiers), falling
  back to constructed `windows.cloud.microsoft/webclient/ent/` URLs. Note: its predecessor
  `getCloudPcLaunchInfo` is deprecated and stops returning data **October 30, 2026** — use only
  `retrieveCloudPcLaunchDetail`.

- **AVD**:

  ```text
  https://windows.cloud.microsoft/webclient/avd/<workspaceID>/<resourceID>
  ```

  with the same optional `?tenant=` and `#loginHint=` parts. Caveats: the required workspace/resource
  ObjectIds are ARM-side identifiers (`Get-AzWvdWorkspace` etc.) that end users cannot self-discover — in
  Phase 0 they must be admin-provisioned (section 11) — and launching a second RemoteApp tab from the same host
  pool disconnects the first.

URL composition rules: query (`?tenant=`) before fragment; `#loginHint=` must be the **last** component. The
web launcher always appends `#loginHint=<active account UPN>` so the browser session follows the active account
(FR-3), and `?tenant=<tenantID>` for accounts whose home tenant differs from the browser's default.

### 5.4 Connectivity reference

Transport sequence (Microsoft-documented): TCP/443 Reverse Connect establishes first; RDP is established; the
client then tries direct UDP Shortpath via STUN, otherwise relayed UDP via TURN; TCP remains the fallback.
([Microsoft Learn][a8]) The UDP transport itself is publicly specified as the Remote Desktop UDP Transport
Extension, including **[MS-RDPEUDP2]**, with multitransport bootstrap in **[MS-RDPEMT]**. ([Microsoft
Learn][a7]) These specs are the basis for the Phase 2 FreeRDP work.

### 5.5 Device and resource redirection

Redirection is a **request, not a guarantee**: AVD and Windows 365 host-side RDP properties can disable any
channel regardless of what the client asks for, so the UI must present redirection state as negotiated, and report
a channel the host refused as an informational condition rather than a client error.

Flag names below are the expected FreeRDP 3 options; each is **verified against `FreeRDP 3.30` at Stage 3** before
being relied on.

| Capability | Phase 1 default | Expected flag | Per-resource override | Sandbox requirement (section 5.8) | Host policy may disable |
| --- | --- | --- | --- | --- | --- |
| Clipboard — text | **On** | `/clipboard` | Yes | none | Yes |
| Clipboard — files | Off | via drive redirection | Yes | filesystem portal | Yes |
| Folder / drive | **Off by default**, explicit folder picker | `/drive:<name>,<path>` | Yes | filesystem portal | Yes |
| Audio out | **On** | `/sound` | Yes | audio socket (PipeWire/Pulse) | Yes |
| Microphone | Off | `/microphone` | Yes | audio socket | Yes |
| Printers | Deferred | `/printer` | — | CUPS socket | Yes |
| Smartcard | Deferred | `/smartcard` | — | PC/SC socket | Yes |
| Camera | Deferred | `/camera` | — | device access | Yes |
| USB | **Out of scope** | `/usb` (urbdrc) | — | not available under Flatpak | Yes |
| Serial / parallel | Out of scope | — | — | — | Yes |

Rationale for the defaults: clipboard text and audio out are what users assume exists and carry no filesystem
exposure. Drive redirection is off by default because it grants the remote host access to local data — an explicit
per-resource folder choice is the only form in which it is offered. Printers, smartcards and camera are deferred on
effort grounds, not feasibility. USB redirection is out of scope because the chosen packaging model cannot grant it
(section 5.8); shipping it would require abandoning Flatpak as primary.

### 5.6 Display and display server

**Phase 1 display scope:** single monitor; windowed and fullscreen; dynamic resolution on window resize where the
FreeRDP client supports it (`/dynamic-resolution`); integer scaling only. Multi-monitor span (`/multimon`),
fractional scaling, and per-monitor DPI are **deferred** with no Phase 1 commitment.

**Display server support matrix**

| | Status | Known limitations |
| --- | --- | --- |
| X11 | **Supported** | Reference platform for Phase 1 |
| Wayland via XWayland | **Supported with limitations** | Keyboard grab and global-shortcut passthrough restricted; cursor confinement weaker; clipboard behavior differs; per-monitor DPI not honored under integer scaling |
| Native Wayland | Not supported in Phase 1 | Tracked as future work; depends on FreeRDP client-backend support |

FreeRDP 3's client backends do not all treat Wayland as a first-class target, and sessions commonly run through
XWayland with the scaling and input-grab caveats above.

**The product ships against `xfreerdp`, the X11 client** (decision **D-20**, section 14.1).

One consequence follows directly, and it makes the table above stronger than a snapshot: `xfreerdp` is an X11 client, so
**on a Wayland session the RDP window always runs through XWayland**. There is no native-Wayland path with this
backend. The limitations listed above are therefore the *permanent* Phase 1 position rather than a temporary state
pending better Wayland support — and the route to changing them is switching backend (to SDL), not waiting.

Confirming the backend's *behavior* — the specific limitations tabulated above, measured rather than assumed — remains a
Stage 3 verification task. The decision and the verification are different things, and only the second one waits. The Wayland limitations above must appear in user-facing
documentation, because they present as "the client is broken" otherwise.

### 5.7 Non-functional requirements

| ID | Requirement |
| --- | --- |
| **NFR-1** | Cold start to an interactive window: ≤ 2 s (p95), excluding first-run interactive sign-in. |
| **NFR-2** | Resource enumeration: ≤ 3 s (p95) for ≤ 25 resources on a broadband connection; the same budget bounds an account switch. |
| **NFR-3** | Native connect: click to visible session window ≤ 12 s (p95) on an already-running Cloud PC, of which ≤ 3 s is connection-config acquisition. |
| **NFR-4** | Idle client footprint ≤ 250 MB RSS, excluding FreeRDP session subprocesses. |
| **NFR-5** | At least 2 concurrent native sessions supported; soft-warn above 4. |
| **NFR-6** | TCP-only acceptability gate — see below. Phase 1 does not proceed past Stage 3 without a recorded result. |
| **NFR-7** | Platform baseline: current and previous Ubuntu LTS, current and previous Fedora, current Debian stable; glibc ≥ 2.35; GNOME and KDE Plasma. Exact versions confirmed at the Stage 1.5 stack gate. |
| **NFR-8** | FreeRDP ≥ 3.30.0 is enforced by a **runtime version check**. Below the floor, the native method is disabled with the specific reason (section 9), never attempted. |

**NFR-6 — TCP-only acceptability gate (measurable).** Phase 1's roadmap gate is stated in section 11 as "if
TCP-only performance is acceptable". That is defined here so it can be passed or failed:

- **Workload:** a scripted office sequence — scroll a 40-page document, sustained typing in a mail client, switch
  between three windows. Full-motion video is explicitly **excluded** and declared out of scope for TCP-only.
- **Conditions:** impairment injected locally (`tc netem`) at RTT 20 / 80 / 150 ms × loss 0 / 0.5 / 1 %.
- **Pass criterion:** at **80 ms RTT and 0.5 % loss**, typed-character echo latency ≤ 150 ms (p95) and no visual
  stall > 500 ms during scroll.
- **Method:** 60 fps screen capture with frame differencing against synthetic input timestamps.
- **Recording:** results at 150 ms / 1 % are recorded as *documented degradation*, not a failure; they determine
  how Phase 2 (Shortpath) is justified and prioritized.

### 5.8 Packaging and distribution

**Decision:** **Flatpak is the primary distribution format.** The driver is section 2.2's requirement to run
FreeRDP ≥ 3.30.0 rather than whatever a distribution ships — which, for most distributions and for some time, means
the application must **bundle** FreeRDP. Flatpak makes that bundling routine and gives one artifact across the
NFR-7 baseline.

| Format | Status | Notes |
| --- | --- | --- |
| Flatpak | **Primary** | Bundles **`xfreerdp`** ≥ 3.30.0 (D-20) and its X11 dependencies; single cross-distro artifact |
| `.deb` / `.rpm` | Secondary | Hard dependency on FreeRDP ≥ 3.30.0; ships only where that is satisfiable |
| AppImage | Not planned | — |
| Distro-packaged FreeRDP | Not relied upon | Runtime check (NFR-8) disables the native path when below the floor |

**Sandbox costs — accepted explicitly**, because several of them bound section 5.5:

| Need | Mechanism | Consequence |
| --- | --- | --- |
| Token cache in the OS keyring | Secret Service portal | Keyring may be unavailable → section 6.3 fallback path and its section 9 error state are load-bearing, not theoretical |
| Web launch | OpenURI portal | Browser choice is the portal's, not the app's |
| Folder / drive redirection | Filesystem portal | Per-folder grants only; whole-home redirection not offered |
| Audio out / microphone | Audio socket | Standard |
| Camera, smartcard | Device / PC/SC access | Contributes to those being deferred in 5.5 |
| USB redirection | — | **Not grantable**; the reason USB is out of scope |
| Native session display | **X11 socket** (`--socket=x11`, or `fallback-x11` plus `wayland`) | Required by D-20's X11 client. On Wayland hosts the session runs through XWayland (§5.6). Verify this is grantable — without it the Flatpak can sign in but cannot open a native session |

**Verification task (Stage 1.5):** confirm each portal cost above against a real Flatpak build before the stack
decision is finalized. Any that proves harder than stated removes a capability from section 5.5, and that trade is
made knowingly rather than discovered late.

### 5.9 Enterprise network environment

The client's target users sit disproportionately behind managed corporate networks, so egress cannot be assumed
direct. Three distinct paths must each work through that environment, and they do not share one configuration:

| Path | Traffic | Proxy handling |
| --- | --- | --- |
| Auth | Entra token endpoints | Honors the system/session HTTP proxy configuration |
| Control plane | Microsoft Graph, AVD feed | Honors the same configuration |
| Session | FreeRDP → ARM gateway, TCP/443 | Must be **passed through to the FreeRDP subprocess explicitly** — it does not inherit the client's HTTP proxy settings |

Requirements:

- **Respect the environment's proxy configuration** (`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` and the desktop's proxy
  settings) for all auth, Graph and feed traffic. Under Flatpak, confirm what the sandbox actually exposes — this is a
  Gate STACK verification item alongside the portal costs above.
- **Propagate proxy configuration to the session**, since the native launcher builds FreeRDP's argument vector
  (section 5.1.1) and is the only component able to do so. A client that authenticates successfully and then fails to
  connect is the confusing failure mode this prevents.
- **Report proxy and TLS-inspection failures distinguishably** from ordinary connectivity failures (section 9);
  "network unreachable" for a blocked proxy sends users down the wrong diagnostic path entirely.
- **Corporate TLS inspection is expected to work**, because validation uses the system trust store rather than pinned
  certificates — see section 10.1 for that decision and the residual risk it accepts.
- **PAC-script and authenticated-proxy support** (NTLM/Kerberos/Negotiate to the proxy itself) is **deferred**, with
  the gap stated in user documentation rather than silently discovered. Revisit if pilot tenants require it.

---

## 6. Identity and session lifecycle

### 6.1 Protocol

- **OAuth 2.0 authorization code flow with PKCE**, Microsoft identity platform v2 endpoints, as a **public
  client** (no client secret).
- Interactive sign-in uses the system browser (preferred) or an embedded webview, with a **loopback redirect
  URI registered as a native/public-client (mobile & desktop) redirect** — explicitly **not** an `spa`
  redirect: SPA-registered redirects cap refresh tokens at 24 hours, which would force daily re-auth.
  ([Microsoft Learn][r3])
- Candidate libraries (non-binding): MSAL (Python/.NET/Node/Java flavors run on Linux) or a minimal in-house
  OAuth 2.0 client following the MSAL cache and silent-acquisition patterns. ([Microsoft Learn][r4])

### 6.2 Scopes and consent

| Scope                   | Used for                              | Notes                                        |
| ----------------------- | ------------------------------------- | -------------------------------------------- |
| `openid profile offline_access` | Sign-in, ID token, refresh tokens | Standard                                     |
| `CloudPC.Read.All`      | FR-1 Cloud PC enumeration             | Delegated; **admin consent required**        |
| `CloudPC.ReadWrite.All` | FR-5 management actions               | Delegated; **admin consent required**; no lesser self-service scope exists |
| AVD feed audience       | FR-1 AVD enumeration, `.rdpw` (§8)    | `https://www.wvd.microsoft.com/.default` (AVD API `9cdead84-…`). Grantability to **our own** registration is the Stage 0 gate; documented fallback is reuse of the first-party AVD client ID `a85cf173-…`, as FreeRDP does (§11) |

Strategy: request `CloudPC.Read.All` at first sign-in; request `CloudPC.ReadWrite.All` via incremental consent
when the user first invokes an action. Both require tenant admin consent, so the app must handle the
unconsented case gracefully (section 9). App-only permissions and personal Microsoft accounts are **not
supported** by the Cloud PC APIs. ([Microsoft Learn][r1])

### 6.3 Token cache

- Per-account cache holding access token, refresh token, ID token, and the account object (home account ID,
  UPN, tenant) — modeled on the MSAL cache schema. ([Microsoft Learn][r4])
- Encrypted at rest via the OS keyring (libsecret/GNOME Keyring or KWallet). **A keyring is required** — there is no
  encrypted-file fallback (decision **D-2**, section 14). Where no Secret Service provider is available the
  application reports that plainly and does not sign in, rather than offering a weaker mode whose guarantee users
  cannot evaluate. A key stored alongside its ciphertext is obfuscation, not encryption, and a passphrase prompt on
  every launch is a UX cost with no corresponding security benefit for a client whose tokens are already short-lived.
- Multi-account: the cache stores any number of accounts; the app enumerates cached accounts at startup to
  rebuild the account switcher, and silent acquisition always names the specific account (MSAL
  `GetAccounts` → `AcquireTokenSilent(account)` pattern). ([Microsoft Learn][r5])
- Refresh tokens rotate on every use; the cache always persists the newest RT immediately.

#### Concurrency — single-flight refresh

FR-4-AC-1 requires a silent acquisition before *every* Graph call, feed query, and launch, while refresh tokens
rotate on every use. Those two facts together are a **race**: two concurrent acquisitions for the same account can
each redeem the current refresh token, each receive a rotated one, and the later write can strand the token the
other caller is about to use. The symptom is a spurious `ReauthRequired` under nothing more unusual than a refresh
and a status poll landing together.

Required:

- **Single-flight per account.** Concurrent silent-acquisition requests for one account collapse into one network
  refresh whose result is shared by all waiters. This is a correctness requirement, not an optimization.
- **Serialized cache writes.** A refresh-token write is atomic with respect to any other write for the same
  account; an interrupted write must never leave the cache without a usable refresh token.
- **Cross-process coordination.** The cache is keyring-backed and therefore shared between processes, so two
  instances of the application race in exactly the same way. Resolved by **single-instance enforcement**: a second
  launch activates the existing instance rather than starting a competing one. If single-instance is ever relaxed,
  a cross-process lock around acquire-and-persist becomes mandatory.
- **Clock-skew tolerance.** Expiry is evaluated with a safety margin rather than against exact `exp`, so a slightly
  skewed local clock does not send a valid token to the refresh path (or a stale one to Graph).

Whether the chosen MSAL flavor provides intra-process single-flight and cross-process cache locking **varies by
flavor and must be verified at Gate STACK** (section 11.1) — it is not safe to assume either. Where the library does
not provide it, the application supplies it.

### 6.4 Token expiry state machine (per account)

```text
                 ┌────────────┐
                 │ SignedOut  │
                 └─────┬──────┘
              Add account / re-auth
                       ▼
              ┌─────────────────┐   success   ┌────────────┐
              │ InteractiveAuth ├────────────►│   Active   │◄─────────────┐
              └───────┬─────────┘             └─────┬──────┘              │
                      │ user cancels /              │ access token       │ success
                      │ hard failure                │ expired/near expiry│ (new AT, rotated RT)
                      ▼                             ▼                    │
                 SignedOut                 ┌────────────────┐            │
                                           │  SilentRefresh ├────────────┘
                                           └───┬───────┬────┘
                 invalid_grant / RT expired    │       │  interaction_required /
                 (90-day inactivity,           │       │  claims challenge (CA/CAE)
                 revocation, pwd change)       │       ▼
                                               │   InteractiveAuth
                                               │   (carrying `claims`,
                                               │    login_hint=<UPN>)
                                               ▼
                                       ┌───────────────┐
                                       │ ReauthRequired│  (non-blocking banner;
                                       └───────────────┘   other accounts unaffected)

        Network failure during SilentRefresh → retry with backoff; keep using an
        unexpired cached AT if one exists; else treat account as Offline, not ReauthRequired.
```

Operating rules:

- Silent acquisition runs **before every Graph call, feed query, and connection launch** — never on a timer
  derived from assumed lifetimes. Access tokens last a randomized 60–90 minutes (CAE-capable clients may
  receive 20–28 h tokens); the cache layer, not the caller, knows when refresh is needed. ([Microsoft
  Learn][r2])
- A `claims` value returned in a challenge is passed through to the interactive request verbatim; a plain retry
  is never sufficient for a claims challenge.
- ReauthRequired affects only the one account: its resources grey out and its banner appears, while the active
  or other accounts continue working.

### 6.5 Conditional Access and device state

Section 6.4 handles token *expiry*. A separate and more consequential class of failure is Conditional Access
policy that this client **cannot satisfy at all**, regardless of how well authentication and the feed work. This is
a scope boundary, not an error to retry.

An unbrokered public-client application on an unregistered Linux desktop does not satisfy:

- **Require compliant device** — needs device enrollment and a compliance signal.
- **Require hybrid Entra joined device** — not achievable on Linux.
- **Require approved client app** / **app protection policy** — enumerates first-party mobile/desktop clients.
- **Require token protection (bound tokens)** — needs a device-bound credential.

Device registration and device-bound SSO on Linux go through Microsoft's Linux identity broker together with
Intune's Linux support, on a limited distribution matrix.

**Decision D-10 (section 14): broker integration is committed, and scheduled after the initial delivery effort.**
The reasoning is market reach rather than technical preference — tenants enforcing device-based Conditional Access are
disproportionately the tenants running Windows 365, so leaving them unsupported indefinitely would mean shipping a
client much of the target market cannot use (risk 10). What is committed is the direction; what is **not** committed
is doing it inside the initial effort, because it is a substantial piece of work (broker IPC, a supported-distro list
narrower than NFR-7, an enrollment prerequisite) and it sits off the critical path to proving native connectivity
works at all.

Sequencing: broker integration follows Phase 1, and its scope is sized only **after** the Stage 0 identity finding —
that result changes what the broker has to do. Until it lands, the support position below applies unchanged.

**Support position until broker integration lands (Phases 0 and 1):** tenants enforcing device-based Conditional
Access on Cloud PC or AVD access are **not supported**. The requirement on the app is to *detect and explain* this,
never to present it as a sign-in failure:

- Map the device-state Entra error codes (the `AADSTS53000` / `AADSTS53001` / `AADSTS530003` family — the exact
  current set is confirmed at Stage 0) to a dedicated UI state that names the cause: the tenant requires a managed
  or compliant device, this client cannot satisfy that, and the web client in a browser on a compliant device is
  the available route.
- Never enter `ReauthRequired` or a retry loop on a device-state block; re-authentication cannot succeed.
- Record the tenant-side requirement in user documentation so evaluators discover it before installing.

**Gate consequence (pre-Stage 0):** determine whether the pilot tenant enforces device-based Conditional Access
**before** running the Stage 0 identity spike. If it does, Stage 0's result is uninterpretable — a failure could be
the app registration, the feed audience, or the policy, and the gate cannot distinguish them. Section 13.1 carries
this as a test-environment prerequisite.

---

## 7. Microsoft Graph integration

### 7.1 Endpoint matrix

| Operation                | End-user path (delegated)                       | API version | Admin path (`/deviceManagement/virtualEndpoint/cloudPCs/{id}`) |
| ------------------------ | ----------------------------------------------- | ----------- | ---------------------------------------------------- |
| List Cloud PCs           | `GET /me/cloudPCs`                              | **v1.0**    | `GET .../cloudPCs` (all tenant Cloud PCs)            |
| Get Cloud PC             | `GET /me/cloudPCs/{id}`                         | v1.0        | `GET .../{id}`                                       |
| Retrieve launch detail   | `GET /me/cloudPCs/{id}/retrieveCloudPcLaunchDetail` | **beta** | `GET .../{id}/retrieveCloudPcLaunchDetail`           |
| Restart                  | `POST /me/cloudPCs/{id}/reboot`                 | **beta**    | `POST .../{id}/reboot` (v1.0)                        |
| Rename                   | `POST /me/cloudPCs/{id}/rename`                 | **beta**    | `POST .../{id}/rename` (v1.0)                        |
| Troubleshoot             | `POST /me/cloudPCs/{id}/troubleshoot`           | **beta**    | `POST .../{id}/troubleshoot` (v1.0)                  |
| Reset / Reprovision      | `POST /me/cloudPCs/{id}/reprovision`            | **beta**    | `POST .../{id}/reprovision` (v1.0)                   |
| Restore to snapshot      | — (none, even in beta)                          | —           | `POST .../{id}/restore` (+ snapshot enumeration)     |
| Resize                   | — (none)                                        | —           | `POST .../{id}/resize`                               |
| End grace period         | — (none)                                        | —           | `POST .../{id}/endGracePeriod`                       |

Permissions: enumeration and launch detail `CloudPC.Read.All`; every action `CloudPC.ReadWrite.All`; both
delegated-only, admin-consent. ([Microsoft Learn][r1])

`retrieveCloudPcLaunchDetail` returns `{cloudPcId, cloudPcLaunchUrl, windows365SwitchCompatible, ...}` and is
the successor to `getCloudPcLaunchInfo`, which is **deprecated and stops returning data October 30, 2026** —
the app must never ship a dependency on the deprecated function. ([Microsoft Learn][r7])

### 7.2 Beta-dependency risk

The `/me` action endpoints exist **only in Graph beta**, which Microsoft marks "subject to change; use in
production applications is not supported." Mitigation: the Management Action service isolates version selection
behind a feature flag; if a beta call fails with a shape/contract error the UI degrades to "Action unavailable —
Microsoft API change" rather than crashing; the project watches for v1.0 promotion of the `/me` action paths
and switches when available.

National-cloud note: Cloud PC Graph APIs are unavailable in US Gov L5 (DOD) and 21Vianet-operated clouds; the
app should detect these environments and disable FR-5 with an explanatory message.

### 7.3 Status refresh

Resource status (e.g., Cloud PC `status`) is polled while the window is foregrounded (suggested: 60 s interval,
paused in background), refreshed immediately after any invoked action, and on manual refresh. All Graph calls
honor `429` + `Retry-After` (section 9).

### 7.4 State-gated availability

Connect methods and management actions are **gated on the resource's current state**. The binding rules:

- A resource whose status is not known to permit connection offers no enabled Connect method, with the reason in
  the tooltip. Unknown or unrecognized status values are treated as **not connectable** — never optimistically
  enabled.
- An action whose precondition the current state does not satisfy is disabled with a reason, rather than offered
  and then failed — the same principle FR-5-AC-3 applies to admin capability.
- A state transition triggered by an action suppresses conflicting actions until the next refresh resolves the new
  state.
- Connecting to a **stopped or deallocated** Cloud PC is a normal first-run case, not an error. FreeRDP already
  models the gateway's orchestration retry (`E_PROXY_ORCHESTRATION_LB_SESSIONHOST_DEALLOCATED`), so the UI shows a
  starting state and waits, subject to a stated timeout. Note that section 7.1 shows **no start/resume action on
  any path**, `/me` or admin — waiting on the gateway is the only mechanism available.

**Outstanding (P1):** the full matrix of Graph `cloudPC` status value × (Connect native · Connect web · Restart ·
Rename · Troubleshoot · Reprovision) enablement, the tooltip string for each disabled cell, and the
deallocated-start timeout value. Its input is the documented `cloudPC` status enum, to be enumerated against v1.0
and confirmed against a live tenant.

---

## 8. AVD feed integration — the connection-config gap

This is the project's **critical-path unknown**, and it carries two responsibilities: AVD **enumeration** (FR-1)
and **connection configuration acquisition** for the native path (FR-2).

**Scope of the gap, precisely.** FreeRDP implements Entra token acquisition, the ARM gateway negotiation
(`POST /api/arm/v2/connections`), reverse connect, and the RDP session. It has no feed-discovery step — it starts
*after* the `.rdpw` already exists. The gap is therefore **one call chain**: feed discovery → workspace feed
download → compose a `.rdpw` from the returned routing fields. It is not a Reverse-Connect, broker, or gateway
reimplementation, and because FreeRDP does not verify the `.rdpw` signature (section 2.2), the composed file needs
no signing authority. The token audience is known (`https://www.wvd.microsoft.com/.default`); what is unknown is
the request/response schema and whether **our own** app registration can obtain that audience — the Stage 0 gate in
section 11.

What Microsoft documents publicly:

- The feed-discovery URL:

  ```text
  https://rdweb.wvd.microsoft.com/api/arm/feeddiscovery
  ```

- That the AVD feed returns the user's remote resources and digitally signed connection configurations, in two
  stages: **initial feed discovery** and **workspace feed download**. ([Microsoft Learn][a5], [a2])

Only the first endpoint is published. The **second-stage workspace-feed URL is expected to be returned by the
discovery response**, and the `.rdpw` routing fields live in that second response — so capturing *both* legs, and
confirming the chaining between them, is an explicit Stage 1 deliverable alongside the field-mapping table.

What is **not** documented: a supported third-party client contract — request/response schemas, token
audience/client-ID requirements — that would let an independent client do
`authenticate → enumerate resources → download current signed .rdpw` in a supported way. The service is not a
black box (endpoints and architecture are visible), but the client-facing feed protocol is not exposed as a
supported Graph-style API.

Several facts sharpen the constraint (re-verified August 2026):

- The manual bootstrap path is **effectively gone**: as of July 2026 the AVD web client URL
  (`client.wvd.microsoft.com/arm/webclient/`) redirects to the generic `windows.cloud.microsoft`, which offers
  **no `.rdpw` download option** at all ([GitHub][n4]); FreeRDP's own AVD FAQ has struck out the old
  "Download the rdp file" instruction. ([GitHub][a3])
- Microsoft has confirmed on Q&A (August 2026) that **no public API exists** to fetch `.rdpw` files; manual
  export from the web client (where still available) is the only supported method, and the file contains a
  `loadbalanceinfo` routing token. ([Microsoft Learn][n5])
- Microsoft's blessed automation alternative is the **URI schemes** `ms-avd:connect` and `ms-rd:subscribe` —
  but these are implemented only by the Windows Remote Desktop client and Windows App (≥ 2.0.804.0), so they
  do not help a Linux client. Their parameter shapes do document how Microsoft identifies a resource.
  ([Microsoft Learn][n6])
- A GitHub-wide code search for `api/arm/feeddiscovery` surfaces only documentation and subscription-config
  scripts — **no public client-side implementation of the ARM feed protocol exists**; the reverse-mapping in
  Phase 1 is original work.
- Graph's `CloudPc.Id` is **not** a substitute for the connection configuration; Graph is control-plane only. However,
  Graph beta `retrieveCloudPcLaunchDetail` (section 7) does return the **rdweb-namespace tenant and resource
  identifiers** for each Cloud PC inside its `cloudPcLaunchUrl` — a concrete, supported anchor for the feed
  reverse-mapping. ([Microsoft Learn][r7])

Reference material for the feed work: the classic RDWeb workspace webfeed XML is publicly specified as
**[MS-TSWP] (Terminal Services Workspace Provisioning Protocol)** — the AVD ARM feed is a different,
undocumented API, but MS-TSWP is its closest documented relative — and the community project **RAWeb**
implements a compatible feed *server* that Windows App can subscribe to, a useful reference for workspace-feed
semantics from the server side. ([Microsoft Learn][n7], [GitHub][n8])

Consequences:

- **Enumeration**: until the feed protocol is reverse-mapped (Phase 1), the app cannot self-enumerate AVD
  resources for an ordinary end user. Phase 0 AVD support is therefore limited to **admin-provisioned
  bookmarks** (workspace/resource IDs supplied by a tenant admin, launched via the web path).
- **Native connections**: Phase 1's first engineering validation stays deliberately small — obtain one valid
  Windows 365 `.rdpw` from a tenant by whatever mechanism is currently available, prove the target stack
  against FreeRDP 3.30.0+ with the upstream AVD flags, and only then invest in reverse-mapping feed
  acquisition. If TCP-only performance is acceptable, most protocol risk is eliminated before touching the
  feed.
- **Risk**: unknown token-audience / first-party-client-ID requirements raise both schedule risk and a
  support/ToS risk (section 12).

---

## 9. Error handling

| Domain | Error | Handling |
| ------ | ----- | -------- |
| Auth | AT expired | Normal: silent refresh (section 6.4), invisible to user |
| Auth | `invalid_grant` / RT expired / revoked | Account → ReauthRequired; per-account banner; other accounts unaffected |
| Auth | `interaction_required` / claims challenge | Interactive re-auth with `claims` + `login_hint`; never plain-retry |
| Auth | User cancels interactive auth | Return to prior state; no error dialog |
| Auth | **Device-state CA block** (`AADSTS53000` family) | Dedicated state naming the cause: tenant requires a managed/compliant device, this client cannot satisfy it, use the web client on a compliant device. **Never** `ReauthRequired`, never retried (section 6.5) |
| Auth | Feed token rejected (`401` at feed) despite successful issuance | Treat as identity-model failure, not expiry: `Unavailable(reason)` to the launcher, native disabled with reason, web fallback offered; log the audience actually issued |
| Graph | `403` insufficient privileges / consent missing | Guided admin-consent flow: explain that a tenant admin must approve, show the admin-consent URL for forwarding |
| Graph | `404` on `/me/cloudPCs` (no license/assignment) | Empty state: "No Cloud PC is assigned to this account" |
| Graph | `429` throttled | Honor `Retry-After`; exponential backoff; no user-visible error unless persistent |
| Graph | Beta contract change (unexpected shape) | Disable affected action with "Microsoft API change" message; log for triage |
| Feed | Discovery/download failure | AVD section shows inline error + retry; Cloud PC section unaffected |
| Config | **Field validation failure** (host outside the allowlist, malformed value) | Reject the configuration; report as a **security** error, not a connection error; no session attempted; retry does not re-offer the same config (section 10.1) |
| Native | **FreeRDP absent, or below the 3.30.0 floor** | Native method disabled with the detected version and the required floor stated; web remains available (NFR-8) |
| Native | Cloud PC stopped / deallocated at connect | Starting state with progress, bounded by the section 7.4 timeout; not surfaced as a failure until the timeout elapses |
| Native | Cloud PC on a private network unreachable from this host | Distinct message naming the network cause; retry offered, web fallback offered |
| Native session | Gateway rejects connection / token rejected mid-session | Toast with reason; offer retry (fresh silent token) and web-launch fallback |
| Native session | FreeRDP session drop | Standard reconnect prompt; the configuration is re-acquired and re-validated before reconnecting (section 5.2) |
| Web launch | Browser fails to spawn | Error toast with the URL offered for manual copy |
| Network | Offline | Offline banner; cached list greyed; connects disabled; auto-recover on connectivity |
| Network | **Proxy required or rejecting** (auth, Graph, or feed) | Distinguished from "unreachable": names the proxy as the cause and shows the configuration the app detected. Never reported as plain offline (section 5.9) |
| Network | **Proxy blocks the session path** but auth and Graph succeed | Names the session-path proxy specifically — the confusing case where sign-in works and connecting does not (section 5.9) |
| Auth | Concurrent-refresh collision | Must not occur: single-flight is required (section 6.3). If observed, it is a defect, not a condition to handle |

Messaging rules: user-visible messages state what happened and what the user (or their admin) can do; raw error
codes go to logs, not dialogs; retries are automatic where safe (throttling, transient network) and manual where
the user should decide (session drop, feed failure).

---

## 10. Security considerations

### 10.1 Connection-configuration trust model

**The `.rdpw` signature is not a trust control for this client.** FreeRDP does not verify it (section 2.2), and the
native path *composes* the configuration from feed data rather than passing through a pre-signed file (section 5.2).
The integrity of every connection-critical field — `full address`, `alternate full address`, `gatewayhostname`,
`armpath`, `loadbalanceinfo` — therefore rests on exactly two things: TLS to the feed, and the validation this
client performs. An adversary able to tamper with the feed response (TLS-inspecting middlebox, mis-issued or
compromised CA, hostile DNS answer) could otherwise steer a native session, with its Entra token, to a host of
their choosing.

Required controls:

- **Strict TLS on all feed and Graph calls**, with **no user-facing bypass**. There is no "continue anyway" for a
  certificate error on the feed path; the native method reports `Unavailable` instead.
- **Validation against the system trust store — and deliberately *not* certificate pinning.** Pinning would be the
  stronger control against the middlebox threat, and it is **rejected anyway**, because it fails closed in precisely
  the environments this client targets: enterprises running Windows 365 commonly TLS-inspect all egress with a
  corporate CA, which a pinned client cannot distinguish from an attack. Pinning would therefore break the product
  for a large share of its intended users while protecting a smaller share. The consequence is accepted explicitly:
  **an adversary who controls a CA the system already trusts is outside what this client can detect**, and integrity
  against feed tampering rests on the two validation controls below rather than on transport identity alone. Revisit
  only if Microsoft publishes a stable pin set, or if a same-CA feed-tampering incident is observed in practice.
- **Allowlist validation of every host-shaped field** returned by the feed, against expected Microsoft domain
  suffixes, **before** it is written into a configuration. A value outside the allowlist is a security error
  (section 9), not a connection error, and the composed file is discarded.
- **Type and shape validation of every other field** consumed from the feed; unknown fields are dropped rather than
  passed through, so the feed cannot inject configuration this client has not reasoned about.
- **Handoff hygiene**: the composed configuration is written to a file with `0600` permissions in a user-private
  directory, deleted once the subprocess has started, and never persisted (section 5.2).

### 10.2 Token storage

Tokens live only in the OS keyring-backed encrypted cache (section 6.3); never in plain files, logs, or environment
variables. Under Flatpak this depends on the Secret Service portal, which may be unavailable — making the section
6.3 fallback and its section 9 error state load-bearing rather than theoretical (section 5.8).

### 10.3 Public client and the loopback redirect

No client secret is embedded; PKCE protects the auth-code exchange. The loopback listener binds **`127.0.0.1`
only** (never `0.0.0.0`), validates `state`, accepts a single response, and times out. The redirect is registered as
a native/public-client redirect, never `spa` (section 6.1, section 12).

### 10.4 Process and argument handling

Because the native launcher runs FreeRDP as a subprocess (section 5.1.1), feed-derived data crosses a process
boundary as arguments and files — a command-injection surface:

- Feed-sourced values are **written into the configuration file**, never concatenated into a command line or shell
  string.
- FreeRDP is executed with an **argument vector**, never via a shell.
- The argument vector is built from a **fixed set of options** the application controls; no feed-sourced string may
  introduce, extend, or alter an option.
- The configuration file path is generated by the application, not derived from any remote value.

### 10.5 Browser handoff

Direct-launch URLs place the UPN in the `#loginHint` fragment. Fragments are not sent to servers in the HTTP
request, but they do enter browser history on a shared desktop — acceptable for a UPN (low sensitivity), documented
here as a deliberate trade-off. Under Flatpak the browser is chosen by the OpenURI portal, not the app.

### 10.6 Destructive actions

Reprovision requires explicit confirmation (section 4.3); the app never batches or auto-retries destructive Graph
actions.

### 10.7 Logging

Tokens, `Authorization` headers, and full `.rdpw` contents are redacted at all log levels; UPNs are redacted at the
default level. Any diagnostics bundle applies these same rules before anything leaves the machine.

### 10.8 Threat model summary

| | |
| --- | --- |
| **Assets** | Refresh and access tokens; the Entra session; connection configurations en route to FreeRDP; UPNs, tenant IDs and resource names |
| **Trust boundaries** | App ↔ Entra; app ↔ Graph; app ↔ AVD feed; app ↔ FreeRDP subprocess; app ↔ OS keyring/portals; app ↔ browser |
| **In scope** | Feed tampering redirecting a session (10.1); local processes reading configs or racing the loopback listener (10.3); argument injection via feed data (10.4); token leakage through logs or bundles (10.7); accidental cross-account action (FR-3) |
| **Out of scope** | A compromised Linux host with the user's privileges; a malicious tenant administrator; attacks on Microsoft's own services; the security of the Microsoft web client during web launch; **an adversary controlling a CA in the system trust store** (the accepted cost of rejecting pinning, 10.1) |

Deliberately accepted: the UPN in browser history (10.5); TCP-only transport until Phase 2; reuse of the
first-party AVD client ID **if** Stage 0 forces it (section 11, section 12).

---

## 11. Roadmap

### 11.1 Numbering

**Phases** are the product delivery axis — what ships to users. **Stages** are the native-path engineering axis and
exist only inside Phase 1. There is one scheme; the design doc's Stages map onto it as follows.

| Phase | Contains | Ships to users |
| --- | --- | --- |
| **−1** — Environment | Test-environment procurement (section 13.1) | No |
| **0** — Web-first client | — | **Yes** |
| **1** — Native MVP (TCP-only) | Stages 0, 1, 2, 3 | **Yes**, at Stage 3 complete |
| **1.5** — Upstream to FreeRDP | Stage 4 | No — maintenance posture |
| **2** — UDP Shortpath | — | Optional |

Two gates sit between stages and are **blocking**:

- **Gate CA (pre-Stage 0)** — establish whether the pilot tenant enforces device-based Conditional Access. If it
  does, Stage 0's result cannot be interpreted (section 6.5).
- **Gate LG-1 (between Stage 0 and Stage 1)** — legal review. **Owner: Kevin Kaminski (interim).** Written position
  required on both exposures: reuse of the first-party AVD client ID, and traffic capture against Microsoft services
  as the Stage 1 method. A negative position sends the project to web-only or to waiting on upstream; it does not get
  worked around (section 12). *Interim* means the gate has an owner and can therefore be cleared, not that the
  position is legally reviewed — escalate to qualified counsel before the project distributes a build or reuses the
  first-party client ID in a shipped artifact.
- **Gate STACK (Stage 1.5, before Stage 2 code)** — language/toolkit decision record, evaluated against MSAL
  support quality on Linux, secret-service bindings, the Flatpak portal costs of section 5.8, the subprocess
  integration mode of section 5.1.1, and contributor availability. Three further decisions are made here because
  work downstream is blocked without them: the **FreeRDP client backend** to ship against (section 5.6 — packaging
  cannot name a binary until this is settled), whether the chosen MSAL flavor supplies **single-flight refresh and
  cross-process cache locking** or the application must (section 6.3), and what **proxy configuration the Flatpak
  sandbox actually exposes** (section 5.9).

**Phase −1 — Test environment (precedes the Stage 0 gate)**
- Procure and document everything in section 13.1. A project whose critical path is empirical cannot start without
  it, and several Phase 1 gates are uninterpretable without specific tenant properties.

**Phase 0 — Web-first client (ship first)**
- Sign-in, multi-account UI and switching (**FR-3 complete**), token lifecycle (**FR-4 complete**).
- **FR-1 partial**: Windows 365 enumeration via Graph v1.0 `GET /me/cloudPCs`. AVD entries limited to
  admin-preconfigured workspace/resource IDs (end users cannot discover them; section 8).
- **FR-2 web-only**: direct-launch `ent/` and `avd/` URLs with `?tenant=` / `#loginHint=`; native option shown
  disabled with reason.
- **FR-5** via beta `/me` actions behind a "beta API" feature flag (section 7.2).
- Lowest risk; entirely on supported/public API surface except the flagged beta actions.

**Phase 1 — Native MVP (TCP-only).** Gate-first: each stage is cheap to abandon and answers one question before
the next begins. Stages are specified in full in the native-connectivity design doc; summarized here with their
gates.

- **Stage 0 — app-identity feasibility spike (the gate).** Can our **own** Entra public client obtain a usable AVD
  feed token (`https://www.wvd.microsoft.com/.default`) and get HTTP 200 from `feeddiscovery`? Tested **from Linux
  with the app's own token**, not with a replayed capture, so that device-binding of the token is caught here rather
  than at Stage 2. Three outcomes:
  1. *Own registration works* → Stage 1 with our own identity.
  2. *Scope not grantable* → fall back to reusing the first-party AVD client ID `a85cf173-…`, as FreeRDP does;
     recorded as an explicit product risk revisited at Stage 4.
  3. **No-go** — neither identity yields a usable feed token, or the token proves device-bound → **native path
     shelved**; the product ships web-only and the question is revisited if FreeRDP upstream lands feed support
     independently.

  **Timebox: 3 working days, hard.** On expiry without a usable feed token, outcome 3 applies automatically — the
  project **commits to a web-only MVP** rather than extending the spike. This is decided in advance precisely so it
  is not relitigated under sunk-cost pressure on day 4: a spike that has not produced an HTTP 200 in three days has
  produced its answer. **Decision owner: Kevin Kaminski.**

  Prerequisite: Gate CA. Exit: a one-page finding naming the working identity, scope and consent path. No product
  code.
- **Gate LG-1** — legal review (section 11.1) before any capture work begins.
- **Stage 1 — feed schema capture.** Pin down the undocumented contract once: both legs (discovery → workspace feed
  download, section 8), the field-mapping table (feed field → `.rdpw` property), and the **staticness test** of
  section 5.2. Also validate the target stack against FreeRDP 3.30.0+ with one obtained `.rdpw` and the upstream
  flags. **This stage's feasibility is itself unproven** — see section 12 for the capture-method risk, its
  prerequisites, and its fallback. Exit: a documented schema, a field-mapping table, and a checked-in feed fixture.
- **Stage 2 — Connection-Config Provider (product code).** Implement `getConnectionConfig` per sections 5.1 and 5.2
  including the section 10.1 validation and the caching rules. Exit: given an account and a resource, the provider
  emits a `.rdpw` FreeRDP accepts, with no manual file handling. Preceded by **Gate STACK**.
- **Stage 3 — FreeRDP handoff.** Launch the subprocess per section 5.1.1 and map its failures onto section 9. Exit:
  **FR-2-AC-4** — a native Cloud PC session from a cold start, TCP-only — plus a recorded **NFR-6** result and the
  section 5.6 display-backend confirmation.

Landing Stages 0–3 completes **FR-1** (feed enumeration for AVD) and enables **FR-2 native** for both providers.

**Phase 1.5 — Upstream to FreeRDP.** Split by what the project controls:

- *Within the project:* open the upstream PR contributing feed/`.rdpw` acquisition; maintain a downstream patch set
  meanwhile; document the identity posture.
- *Not within the project:* maintainer acceptance, and its timing.

**Upstreaming is therefore never a precondition for shipping**, and the ToS mitigation for first-party-ID reuse
must not depend on it alone (section 12).

**Phase 2 — UDP Shortpath (separate project)**
- Implement **[MS-RDPEMT]** / **[MS-RDPEUDP2]** client-side UDP multitransport in FreeRDP: direct Shortpath via
  STUN, relayed fallback via TURN. FreeRDP's long-standing upstream UDP issue remains open — enabling
  `+multitransport` today negotiates and then falls back because client UDP is unimplemented. ([GitHub][a9])
- Until this lands, native sessions stay on the TCP/443 reverse-connect gateway. Explicitly optional and
  decoupled from Phase 1 delivery. The **NFR-6** result recorded at Stage 3 is what justifies and prioritizes this
  phase.

### 11.2 Traceability

Verification codes: **U** unit/fixture · **I** integration against a live tenant · **M** manual · **C** code review
· **P** measurement.

| Requirement | Criteria | Phase | Verification |
| --- | --- | --- | --- |
| FR-1 | AC-1 … AC-4 | 0 | U (paging, empty/404 shapes) + I |
| FR-1 | AC-5 | 0 partial · 1 (Stage 2) complete | I |
| FR-2 | AC-1 … AC-3 | 0 | U (URL composition) + M |
| FR-2 | AC-4 | 1 (Stage 3) | M + P (NFR-3) |
| FR-2 | AC-5 | 0 | M |
| FR-2 | AC-6 | 1 (Stage 2) | U — hostile-feed fixtures (section 13.3) |
| FR-3 | AC-1 … AC-5 | 0 | I (two tenants) + M |
| FR-4 | AC-1 | 0 | C + instrumented test asserting acquisition precedes every outbound call |
| FR-4 | AC-2 | 0 | I |
| FR-4 | AC-3 | 0 | I — revoke the refresh token server-side |
| FR-4 | AC-4 | 0 | U (synthesized claims challenge) + I where tenant policy permits |
| FR-4 | AC-5 | 0 | U — network fault injection |
| FR-4 | AC-6, AC-7 | 0 | U — N simultaneous acquisitions assert one refresh; M — second-launch activation |
| Section 5.9 proxy paths | — | 0 (auth, Graph, feed) · 1 (session) | I behind a proxy + M; includes the auth-succeeds-connect-fails case |
| FR-5 | AC-1, AC-2 | 0 | I + M |
| FR-5 | AC-3 | 0 | I with both an admin-capable and a plain account |
| FR-5 | AC-4 | 0 | I in an unconsented tenant |
| FR-5 | AC-5 | 0 | U — mutated response shapes |
| FR-5 | AC-6 | 0 | Documented only; no national-cloud test environment assumed |
| NFR-1 … NFR-5 | — | 0 / 1 | P |
| NFR-6 | — | 1, blocking gate | P per section 5.7 |
| NFR-7 | — | 1 | M across the platform matrix |
| NFR-8 | — | 1 | U + M (below-floor and absent FreeRDP) |
| Section 10.1 controls | — | 1 (Stage 2) | U (hostile fixtures) + C |
| Section 10.4 controls | — | 1 (Stage 2) | C + U (injection-shaped feed values) |

---

## 12. Risks and open questions

1. **Admin-consent wall.** Both CloudPC delegated scopes require tenant admin consent; the app is unusable in a
   tenant until an admin approves, and personal Microsoft accounts are unsupported entirely. A first-run
   "request admin approval" UX (section 9) is mandatory, not optional.
2. **Beta-only self-service actions.** All `/me/cloudPCs/{id}/*` actions are Graph beta — "production use not
   supported." Feature-flagged with graceful degradation (section 7.2); watch for v1.0 promotion.
3. **"Reset" is only partially self-service.** Restore-to-snapshot and resize have no `/me` path even in beta
   and require admin virtualEndpoint APIs (plus snapshot enumeration). Oddly, destructive **reprovision is**
   self-service in beta — hence the strong confirmation gate. The UI must distinguish end-user vs admin
   capability per account.
4. **AVD feed protocol undocumented for third parties.** Both AVD enumeration and connection-config acquisition
   depend on mapping it; the unknown is the request/response schema and whether our own registration can obtain the
   (known) feed audience — the Stage 0 gate. This is the roadmap's critical path, and it hardened in July 2026: the
   manual `.rdpw` download was removed from the web client, Microsoft confirmed no public API exists, and no public
   client-side implementation of the ARM feed protocol could be found anywhere on GitHub (section 8). Even the one
   `.rdpw` needed for Stage 1 stack validation is now nontrivial to obtain.
   **4a. The Stage 1 capture method is itself unproven** and may be the real blocker rather than the schema:
   - It requires a **Windows host running Windows App ≥ 2.0.804.0** with a subscribed tenant — Windows App does not
     run on Linux, so this is an unavoidable prerequisite on the critical path (section 13.1).
   - If Windows App pins certificates or binds the feed token to the device, TLS interception yields nothing.
     **Fallback:** drive the feed directly with the Stage 0 token and iterate against its 4xx responses, treating
     error shapes as the schema signal. If that also fails, Stage 1 is a no-go by the Stage 0 rules.
   - **The web client is not expected to be a valid witness**: it uses `/api/arm/weblaunch/…`, which the design work
     concluded is irrelevant to the native path. Windows App is likely the only capture source.
   - The capture must be performed against a tenant the project controls, with the tenant owner's consent
     documented, and only after Gate LG-1 (section 11.1).
5. **Phase-0 AVD discovery gap.** Direct-launch AVD IDs are ARM-derived (admin RBAC); end users cannot
   self-enumerate AVD until the feed work lands, so Phase 0 AVD support is admin-provisioned bookmarks only.
6. **Redirect-registration trap.** Registering the redirect URI as `spa` silently caps refresh tokens at 24
   hours; the registration must be a native public-client loopback redirect to get standard (90-day-inactivity)
   refresh-token behavior. ([Microsoft Learn][r3])
7. **URL/API churn.** `windows.cloud.microsoft` direct-launch URLs are a documented end-user feature (updated
   July 2026), not a versioned API contract; Cloud PC Graph APIs are absent in DOD/21Vianet clouds. Concrete
   evidence of churn: `getCloudPcLaunchInfo` is deprecated with a hard stop on **October 30, 2026**
   (successor: `retrieveCloudPcLaunchDetail`), and the AVD web client's `.rdpw` export disappeared with the
   July 2026 `windows.cloud.microsoft` migration.
8. **CAE/claims challenges.** Graph may issue claims challenges mid-session; the token state machine must
   round-trip the `claims` parameter (section 6.4) — a plain retry loop will spin forever.
9. **Second-RemoteApp caveat.** Launching a second AVD RemoteApp web tab from the same host pool disconnects
   the first; the web launcher should warn when this applies. The native path needs its own answer to the same
   constraint.
10. **Device-based Conditional Access may exclude the client from its own target market.** Tenants requiring a
    compliant, hybrid-joined, or approved-client device block this client no matter how well the feed work goes —
    and those are disproportionately the tenants running Windows 365. This can invalidate the native path
    *independently of everything else in this document*. Mitigation: honest detection and messaging (section 6.5);
    **broker integration is committed (decision D-10) and scheduled after Phase 1** — so the exposure is bounded in
    time rather than permanent. **Residual until it lands: the addressable tenant population is far smaller than the
    Linux desktop install base suggests**, and that constrains any pilot or evaluation run before broker support
    exists. Gate CA (section 11.1) establishes the pilot tenant's posture before Stage 0 so that gate stays
    interpretable.
11. **The trust anchor is this client, not Microsoft's signature.** FreeRDP does not verify the `.rdpw` signature and
    the native path composes the file itself, so feed *tampering* — not merely feed change — is a live threat.
    Mitigation: section 10.1 — strict TLS against the system trust store, host allowlisting, field validation.
    **Pinning is rejected, not deferred**: it fails closed under the corporate TLS inspection that this client's target
    enterprises routinely run, so it would cost more users than it protects. Accepted residual: an adversary holding a
    CA the system already trusts is outside detection, and integrity then rests entirely on field validation.
12. **The routing blob may not be static.** The cacheability premise is unverified, and this spec contradicts it in
    two places: Microsoft describes `loadbalanceinfo` as a routing *token*, and section 9 assumes configurations can
    expire. Mitigation: section 5.2 defaults to no persistence and per-launch re-fetch until the Stage 1 staticness
    test passes. Residual if wrong and undetected: intermittent connection failures — the worst class to diagnose in
    the field.
13. **The FreeRDP version floor is a distribution problem, not a build flag.** Section 2.2 requires ≥ 3.30.0 while
    distributions ship older, so the app must bundle FreeRDP (section 5.8) and enforce the floor at runtime (NFR-8).
    Bundling via Flatpak in turn removes USB redirection and constrains other channels (section 5.5) — a trade
    accepted knowingly here rather than discovered late.
14. **Post-cutoff facts need re-verification before they are relied on.** Several load-bearing claims here are dated
    July–August 2026: removal of the web-client `.rdpw` download; Microsoft's Q&A answer that no public API exists;
    FreeRDP 3.30.0's contents and release date; FreeRDP's signature-ignoring behavior in `arm.c`; the first-party
    client ID and feed scope values; and the `getCloudPcLaunchInfo` hard stop of 2026-10-30. **Re-verify all of them
    as the first task of Stage 0**, since the plan's shape depends on them. A volatile-facts register with owners and
    review dates is outstanding work.

---

## 13. Test strategy

### 13.1 Test-environment prerequisites (Phase −1)

This project's critical path is empirical, so the environment below is **work that precedes the Stage 0 gate**, not
setup to be improvised during it. Each row is either procured or explicitly waived with a stated consequence.

| # | Prerequisite | Needed for |
| --- | --- | --- |
| E-1 | Tenant with Windows 365 Enterprise licences and ≥ 1 provisioned Cloud PC | FR-1, FR-2, FR-5 |
| E-2 | AVD host pool with a published desktop **and** a RemoteApp, in a workspace | FR-1 AVD, FR-2 AVD, section 8 |
| E-3 | Ability to **grant tenant admin consent** — both CloudPC scopes require it (section 6.2) | Everything; without it the app cannot run at all |
| E-4 | A second tenant, or a guest/B2B account | FR-3, `?tenant=` behavior |
| E-5 | An admin-capable account **and** a plain end-user account | FR-5-AC-3 both ways |
| E-6 | A tenant **without** device-based Conditional Access — and ideally one with | **Gate CA**; section 6.5. Without E-6 the Stage 0 result is uninterpretable |
| E-7 | A **Windows host running Windows App ≥ 2.0.804.0**, subscribed to E-1/E-2 | Stage 1 capture (risk 4a). Unavoidable: Windows App has no Linux build |
| E-8 | Linux test matrix: NFR-7 distributions × X11 and Wayland × GNOME and KDE | NFR-7, section 5.6 |
| E-9 | Network impairment tooling (`tc netem`) and 60 fps capture | NFR-6 gate measurement |
| E-10 | A tenant the project **controls**, with the owner's documented consent to capture | Stage 1 legality (Gate LG-1) |
| E-11 | An unconsented tenant, or a way to revoke consent | FR-5-AC-4, the guided-consent path |

**E-3 and E-6 are the two that can stop the project before it starts** — one because nothing works without consent,
the other because the Stage 0 gate cannot be read without it.

### 13.2 Test levels

| Level | Scope | Runs in CI |
| --- | --- | --- |
| **Unit / fixture** | URL composition; feed parsing; `.rdpw` composition; section 10.1 validation; token state machine (section 6.4) with synthesized responses; Graph response shapes including paging, `404`, `429`, mutated beta shapes | **Yes** — the only native-path logic that can be |
| **Integration (live tenant)** | Enumeration, silent refresh, re-auth, consent paths, management actions | No — requires E-1…E-5, E-11; run on demand and before a release |
| **Manual** | Native session establishment, display and redirection behavior, Wayland limitations, account switching UX | No |
| **Measurement** | NFR-1…NFR-6 | No — recorded per release |
| **Code review** | FR-4-AC-1 (acquisition precedes every call), section 10.4 argument handling | Reviewed, plus the tests those criteria name |

### 13.3 Fixtures

Checked into the repository, and the only way the native path gets CI coverage:

- A captured **feed discovery** response and a captured **workspace feed** response (Stage 1 output), redacted per
  section 10.7.
- A known-good composed `.rdpw`, asserted to contain every field FreeRDP reads: `gatewayhostname`,
  `loadbalanceinfo`, `armpath`, `geo`, `full address`, `remoteapplicationprogram`.
- **Hostile fixtures** — feed responses carrying an out-of-allowlist `full address`, a malformed `gatewayhostname`,
  an unknown injected field, and option-injection-shaped values. These verify FR-2-AC-6 and sections 10.1/10.4, and
  are as important as the valid fixture.
- Graph fixtures: paged `/me/cloudPCs`, `404` no-licence, `403` unconsented, `429` with `Retry-After`, and a beta
  action response with a changed shape.

### 13.4 CI boundary

CI runs unit/fixture tests only. Everything touching a live tenant, a real session, or a display server is
on-demand and release-gating but not per-commit. The Connection-Config Provider is therefore designed so its parser
and validator are exercisable **purely from fixtures**, with no network (section 5.1) — this is the reason its
interface is `getConnectionConfig(account, resourceRef)` returning a value rather than performing the launch.

### 13.5 Native manual test plan (Stage 3)

1. Cold start → sign in → enumerate → **Connect (Native)** on a running Cloud PC; observe the session window and a
   successful `POST /api/arm/v2/connections` in FreeRDP trace output.
2. Connect to a **stopped/deallocated** Cloud PC; observe the starting state, the wait, and the timeout (section 7.4).
3. Kill the gateway path mid-session; observe reconnect and config re-acquisition (section 9).
4. Present each hostile fixture through the provider; confirm a **security** error and that no session is attempted.
5. Downgrade FreeRDP below 3.30.0 and remove it entirely; confirm NFR-8 behavior in both cases.
6. Repeat 1 on each E-8 platform combination; record the Wayland limitations actually observed against section 5.6.
7. Run the NFR-6 measurement procedure and record the result; it gates Phase 1 completion.

---

## 14. Decision register

Every entry here is **normative** — where a decision fills a gap, this is the specification of it; where it changed
earlier text, that text has been amended and cross-references the decision ID. Each carries a revisit trigger, so
reversing one is a deliberate act with a stated cause rather than a drift.

### 14.1 Architecture and packaging

| ID | Decision | Rationale | Revisit if |
| --- | --- | --- | --- |
| **D-1** | FreeRDP runs as a **subprocess**; `libfreerdp` is not linked (§5.1.1) | CLI flags are the documented upstream contract; keeps the language choice open; isolates session crashes | Embedded/tabbed session windows become a requirement, or exit codes cannot satisfy §9 |
| **D-3** | **Flatpak is the primary distribution format** (§5.8) | The only routine answer to §2.2's FreeRDP ≥ 3.30.0 floor, which requires bundling | Portal costs prove worse than §5.8 asserts |
| **D-4** | Feed TLS validates against the **system trust store; certificate pinning is rejected** (§10.1) | Pinning fails closed under the corporate TLS inspection this client's target enterprises routinely run — it would cost more users than it protects | Microsoft publishes a stable pin set, or same-CA feed tampering is observed in practice |
| **D-5** | Connection configs are re-fetched on **first launch per resource per session**, then cached in memory; never persisted (§5.2) | Resolves the NFR-3 budget against the no-persistence rule at zero security cost — the cache is already process-scoped | The Stage 1 staticness test passes, which would permit a stated TTL |
| **D-16** | **Python 3 + GTK4/libadwaita** via PyGObject | D-1's subprocess launcher removed the only hard pull toward a compiled language, leaving speed-to-working-client as the deciding factor — which matters under D-14's 3-day spike, since the Stage 0 script grows into the auth layer rather than being thrown away. GTK4 also has the strongest Flatpak story, which D-3 already depends on, and PyGObject gives UI and libsecret through one binding layer | **NFR-1 (≤ 2 s cold start) or NFR-4 (≤ 250 MB RSS) fail on measurement** — PyGObject startup is not free and neither is assumed to pass. Fallback: .NET + Avalonia + MSAL.NET, accepting a less native Linux feel for a single-file publish |
| **D-17** | **MSAL Python**, with `msal-extensions` for the persisted cache | Follows from D-16. `msal-extensions` supplies a keyring-backed cache with cross-process locking — the one place a library answers §6.3's concurrency requirement instead of the application writing it. Reimplementing token refresh is a bad place to be original | Its current Linux/libsecret behavior fails verification, in which case the application supplies single-flight and locking itself as §6.3 already requires |
| **D-20** | The native launcher runs **`xfreerdp`**, FreeRDP's X11 client (§5.6) | More mature than the SDL client and the richest redirection support, which matters because §5.5's channel set is the user-visible half of the product. SDL's advantage is a native-Wayland path, and §5.6 defers native Wayland — so that advantage buys nothing in Phase 1 while costing maturity now. The engine itself was never open: D-1 and §5.2 record FreeRDP as the only open-source stack with working AVD ARM-gateway support | Native Wayland becomes a Phase 1+ target, or `xfreerdp` proves deficient on a §5.5 channel the product needs — in either case the migration is to the SDL client, not to a different engine |
| **D-18** | **Single asyncio event loop**; per-account task groups keyed by home account ID; blocking keyring and subprocess calls in a thread executor | Follows from D-16. Task groups give FR-3-AC-2's cancellation-on-account-switch directly, and a per-account lock plus shared in-flight future gives §6.3 single-flight. GTK application uniqueness supplies FR-4-AC-7 nearly free | Cancellation proves insufficient for in-flight Graph actions rather than just enumeration |

### 14.2 Identity and security

| ID | Decision | Rationale | Revisit if |
| --- | --- | --- | --- |
| **D-2** | A **keyring is required**; no encrypted-file fallback (§6.3) | A key beside its ciphertext is not encryption, and a per-launch passphrase buys no real protection for short-lived tokens. Failing honestly beats a mode users cannot evaluate | A credible sandboxed alternative appears, or Flatpak verification shows the Secret Service portal is commonly unavailable |
| **D-6** | Loopback redirect uses an **ephemeral port** (§6.1, §10.3) | Removes the port-conflict failure mode entirely | Entra's loopback port-matching behavior turns out to require a fixed port — confirmed as a Stage 0 sub-task |
| **D-7** | Admin capability is detected from **token role/`wids` claims** (§4.3, §7.1) | No extra call, no extra consent scope, and no `403` in every tenant's audit log at every startup. Accepts false negatives for Intune-RBAC-only admins, whose effect is that Restore/Resize stay hidden — the direction FR-5-AC-3 already mandates as correct | False negatives prove common enough to matter in a real tenant |
| **D-10** | **Broker integration is committed**, scheduled after Phase 1 (§6.5) | Device-CA-enforcing tenants are disproportionately the Windows 365 population; leaving them permanently unsupported would ship a client much of the market cannot use | Scope is sized after the Stage 0 finding, which changes what the broker must do |

### 14.3 Product scope and process

| ID | Decision | Rationale | Revisit if |
| --- | --- | --- | --- |
| **D-8** | Action completion is observed by **polling** via §7.3's existing refresh, with a stated timeout — no operation-polling machinery (§7.1) | Satisfies FR-5-AC-1 with no new mechanism; the post-action refresh already exists | Graph action responses turn out to carry a pollable operation that materially improves feedback |
| **D-9** | **Windows 365 Enterprise only** for Phases 0–1. Frontline (shared and dedicated), Business and Dev Box are out of scope (§1) | Frontline shared has different connect semantics; Dev Box is a separate devcenter API surface. Keeps the verification matrix small | After Stage 3, once the Enterprise path is proven |
| **D-11** | **No telemetry.** Diagnostics are opt-in, user-triggered bundles applying §10.7 redaction | Easiest privacy posture to defend, and bundles are what a client depending on an undocumented endpoint actually needs in the field | Never, for automatic telemetry; bundle scope may expand |
| **D-12** | Project licensed **Apache-2.0** | Aligns with FreeRDP for the Phase 1.5 upstreaming goal; an unlicensed repository cannot contribute code anywhere | — |
| **D-13** | Persisted state is **JSON under `XDG_STATE_HOME` with a `schemaVersion` per store** and forward-only migrations; tokens remain in the keyring (§6.3) | Migration path exists from version 0, avoiding a future "sign in again and lose your settings" release | A store outgrows flat files |
| **D-14** | **Stage 0 is timeboxed to 3 working days.** On expiry without a usable feed token, the project commits to a **web-only MVP** (§11.1) | Decided in advance so it is not relitigated on day 4. A spike with no HTTP 200 in three days has produced its answer | — |
| **D-19** | **Kevin Kaminski creates and owns the multi-tenant Entra app registration** (§6.1). The registration's home tenant, its recovery path, and its location are recorded at creation (Stage 0, day 1) | A distributable product needs a registration someone owns, and the owner question does not depend on the Stage 0 branch — so it is answered now rather than waiting. **Critical constraint: an Entra app registration cannot be transferred between tenants.** The home tenant chosen at creation is fixed for the life of that client ID, and the client ID ships inside every binary — so relocating later means a new client ID and a forced update for every installed client | Never for the existing client ID — it cannot move. A change of home tenant is a **new registration and a breaking release**, so it happens only if ownership must transfer (the project becoming an organization or community project) and is planned as a release, not a migration |
| **D-15** | The initial effort is a **5-working-day feasibility sprint**, not a Phase 0 delivery: 3 days of Stage 0 (D-14) plus 2 days of skeleton on whichever branch results, then **re-plan with the finding in hand** | Five days answers the question that governs every later estimate. It is explicitly **not** enough for Phase 0 as specified in §11 — multi-account (FR-3), the full token state machine (FR-4), management actions (FR-5) and Flatpak packaging are all outside it. Recording the scope prevents "five-day sprint" being misread later as "Phase 0 in five days" | Re-plan at sprint close. Phase 0's own scope is unchanged by this decision — only the sequencing |

### 14.4 Open — not yet decided

| Question | Blocking | Owner |
| --- | --- | --- |
| ~~Gate LG-1 legal-review owner~~ — **assigned: Kevin Kaminski (interim)**, §11.1. Escalation to qualified counsel still open before any distributed build | — | Kevin Kaminski |
| ~~Stage 0 decision owner~~ — **assigned: Kevin Kaminski**, §11.1 | — | Kevin Kaminski |
| ~~FreeRDP client backend~~ — **decided: `xfreerdp`** (D-20). **Gate STACK's decisions are now complete** | — | Settled |
| Gate STACK **verifications**, all that remain of the gate: `msal-extensions` locking on Linux (V1), Flatpak portal/proxy/X11-socket exposure (V2), NFR-1/NFR-4 measured against PyGObject (V3). These *confirm or overturn* rather than decide | D-16, D-17, D-3 each carry a revisit trigger one of these can fire | Kevin Kaminski |
| ~~Multi-tenant app registration ownership~~ — **assigned: Kevin Kaminski** (D-19). Still to settle at creation: home-tenant choice (irreversible), break-glass recovery, and whether publisher verification is pursued | — | Kevin Kaminski |
| Live-session behavior when the client quits | Stage 3 session work | Deferred with trigger |

---

## 15. References

### Microsoft — connectivity and product
[1]: https://learn.microsoft.com/en-us/windows-365/enterprise/understanding-remote-desktop-protocol-traffic "Understanding Network Flows - Remote Desktop Protocol | Microsoft Learn"
[2]: https://learn.microsoft.com/en-us/windows-365/end-user-access-cloud-pc "Accessing Cloud PCs | Microsoft Learn"
[a2]: https://learn.microsoft.com/en-us/azure/virtual-desktop/network-connectivity "Understanding Azure Virtual Desktop network connectivity | Microsoft Learn"
[a5]: https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-remotedesktop "RemoteDesktop Policy CSP | Microsoft Learn"
[a6]: https://learn.microsoft.com/en-us/windows-app/direct-launch-urls "Access desktops and apps using direct launch URLs for Windows App in a web browser | Microsoft Learn"
[a7]: https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpeudp2/e7c5f83e-6574-4f10-90b0-f51b6f72c752 "[MS-RDPEUDP2]: Introduction | Microsoft Learn"
[a8]: https://learn.microsoft.com/en-us/windows-365/enterprise/understanding-remote-desktop-protocol-traffic "Understanding Network Flows | Microsoft Learn"

### Microsoft — Graph and identity
[4]: https://learn.microsoft.com/graph/api/resources/cloudpc-api-overview?view=graph-rest-beta "Working with Windows 365 Cloud PCs using the Microsoft Graph API | Microsoft Learn"
[r1]: https://learn.microsoft.com/en-us/graph/api/user-list-cloudpcs?view=graph-rest-1.0 "List cloudPCs for user (v1.0) | Microsoft Learn"
[r1b]: https://learn.microsoft.com/en-us/graph/api/cloudpc-reboot?view=graph-rest-beta "cloudPC: reboot (beta, /me path) | Microsoft Learn"
[r1c]: https://learn.microsoft.com/en-us/graph/api/cloudpc-restore?view=graph-rest-beta "cloudPC: restore (admin path only) | Microsoft Learn"
[r1d]: https://learn.microsoft.com/en-us/graph/api/resources/cloudpc?view=graph-rest-v1.0 "cloudPC resource type (v1.0) | Microsoft Learn"
[r2]: https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens "Microsoft Entra access tokens | Microsoft Learn"
[r3]: https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens "Microsoft Entra refresh tokens | Microsoft Learn"
[r3b]: https://learn.microsoft.com/en-us/entra/identity-platform/configurable-token-lifetimes "Configurable token lifetimes | Microsoft Learn"
[r4]: https://learn.microsoft.com/en-us/entra/identity-platform/msal-acquire-cache-tokens "Acquire and cache tokens with MSAL | Microsoft Learn"
[r5]: https://learn.microsoft.com/en-us/entra/msal/dotnet/acquiring-tokens/acquire-token-silently "AcquireTokenSilent / multi-account pattern | Microsoft Learn"
[r6]: https://learn.microsoft.com/en-us/graph/permissions-reference "Microsoft Graph permissions reference | Microsoft Learn"
[r7]: https://learn.microsoft.com/en-us/graph/api/cloudpc-retrievecloudpclaunchdetail?view=graph-rest-beta "cloudPC: retrieveCloudPcLaunchDetail (beta) | Microsoft Learn"
[r7b]: https://learn.microsoft.com/en-us/graph/api/cloudpc-getcloudpclaunchinfo?view=graph-rest-beta "cloudPC: getCloudPcLaunchInfo (deprecated, stops Oct 30 2026) | Microsoft Learn"

### August 2026 research pass — SDKs and connection documentation
[n1]: https://github.com/Devolutions/IronRDP "IronRDP — Rust implementation of RDP | GitHub"
[n2]: https://learn.microsoft.com/en-us/previous-versions/remote-desktop-client/overview "Remote Desktop client overview (previous-versions) | Microsoft Learn"
[n3]: https://learn.microsoft.com/en-us/azure/virtual-desktop/rdp-properties "Supported RDP properties — Azure Virtual Desktop | Microsoft Learn"
[n4]: https://github.com/FreeRDP/FreeRDP/issues/13094 "RDPW file can no longer be downloaded · Issue #13094 · FreeRDP/FreeRDP"
[n5]: https://learn.microsoft.com/en-us/answers/questions/5562259/is-there-a-public-api-to-download-rdpw-files-for-a "Is there a public API to download .rdpw files for AVD? | Microsoft Q&A"
[n6]: https://learn.microsoft.com/en-us/azure/virtual-desktop/uri-scheme "URI schemes with the Remote Desktop client (ms-avd / ms-rd) | Microsoft Learn"
[n7]: https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tswp/1fc83092-67b5-4091-bd6f-256ce6658e80 "[MS-TSWP]: Terminal Services Workspace Provisioning Protocol | Microsoft Learn"
[n8]: https://github.com/kimmknight/raweb "RAWeb — web interface / workspace feed server for RemoteApps | GitHub"

### FreeRDP
[a1]: https://github.com/FreeRDP/FreeRDP/issues/11951 "Azure Cloud PC/AVD RD Gateway sometimes rejects FreeRDP · Issue #11951"
[a3]: https://github.com/freerdp/freerdp/wiki/FAQ "FAQ · FreeRDP/FreeRDP Wiki"
[a4]: https://github.com/FreeRDP/FreeRDP/releases "Releases · FreeRDP/FreeRDP"
[a9]: https://github.com/FreeRDP/FreeRDP/issues/4978 "UDP support for FreeRDP · Issue #4978"
