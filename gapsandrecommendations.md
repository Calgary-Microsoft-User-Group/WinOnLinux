# Gaps and Recommendations — WinOnLinux Specification Assessment

**Assessed artifacts:** `spec.md` (740 lines, §1–§13), `docs/superpowers/specs/2026-08-18-windows365-native-connectivity-design.md` (210 lines), `README.md`
**Assessment date:** 2026-09-12
**Lens:** specification *completeness* and *feasibility* — is this document set sufficient to hand to an implementation team, and is what it describes buildable?
**Not assessed:** code (none exists), UX aesthetics, business case.

> **Status: all P0 recommendations (R1–R13) have been applied to `spec.md`.** The findings below are retained as the
> record of *why* those changes were made, and as the outstanding P1/P2 backlog. Two P0 items required committing to
> a decision rather than only documenting a gap — **FreeRDP integration mode** (subprocess, `spec.md` §5.1.1) and
> **packaging** (Flatpak primary, §5.8) — both written as labeled decision records with rationale and revisit
> conditions so they can be overridden. Three P0 items are only *partly* closable by editing a document: R3's
> environment is specified (§13.1) but not procured, R4's Conditional Access question is specified as a blocking gate
> (§6.5, §11.1 Gate CA) but not answered, and R11's legal review has a gate (§11.1 Gate LG-1) but no named owner.
> Those three carry `<TBD>` markers in the spec. R5 also touches the design doc's Stage 1, which has **not** been
> edited — the capture-method risk landed in `spec.md` §12.4a instead.
>
> **A second pass then found 11 more findings (G-44 … G-54), recorded in section 8A.** Three were promoted to P0 and
> are also applied: **G-45** (circular dependency between display and packaging), **G-47** (strict TLS would break the
> app in TLS-inspecting enterprises — pinning now rejected, and proxy support added as §5.9), and **G-48** (token
> refresh had no single-flight story — a latent correctness bug, not a doc gap). The remainder is tracked in Linear:
> the Implementation project for spec and decision work, the Prerequisites project for environment procurement.

---

## 1. Verdict

The document set is unusually strong on the hard part. The research is real: it correctly identifies Reverse Connect as the reason `xfreerdp /v:host` cannot work, correctly scopes the remaining work to *one* missing step (programmatic acquisition of the connection-config blob) rather than reimplementing a gateway, and the gate-first Stage 0–4 plan in the design doc is the right shape for a project whose critical path is an undocumented protocol. The risk register is honest rather than promotional, and date-stamped evidence is cited for the volatile claims.

What is missing is almost everything *around* that critical path. Three structural problems dominate:

1. **The spec and the design doc disagree**, and the design doc says so itself — its final section is a to-do list of spec edits that were never applied. The spec still describes a pre-design world in places, including a security model that the design doc contradicts outright.
2. **The spec covers control plane only.** Enumerate, authenticate, launch, manage. It is silent on what happens *inside* a session — clipboard, drives, audio, printers, multi-monitor, HiDPI, keyboard layout, reconnect — which is the majority of what users judge a remote-desktop client on, and which is where FreeRDP-on-Linux has real, known sharp edges.
3. **There is no delivery substrate.** No language/toolkit decision, no packaging story, no test-environment prerequisites, no acceptance criteria, no non-functional requirements, no license file. For a project that is *entirely* empirical on its critical path, the absence of a stated test environment is the single most schedule-relevant gap.

Feasibility judgment: **Phase 0 (web-first) is clearly feasible.** **Native (Stage 0–3) is plausible but has two unexamined blockers** that could invalidate it independent of the feed protocol — Conditional Access device compliance, and the capture dependency in Stage 1. **Stage 4 (upstreaming) is a governance dependency on a third party** and should not appear in a delivery plan as a stage with an exit criterion the project cannot unilaterally meet.

Findings below are IDed (`G-nn`) and severity-tagged: **P0** = resolve before writing product code; **P1** = resolve before the corresponding phase ships; **P2** = hygiene, resolve opportunistically.

---

## 2. Document coherence and drift

### G-01 — The design doc's own "Impact on spec.md.txt" section was never applied · **P0**

The design doc (line 205) lists required spec edits: §8 (gap is one feed-discovery step), §5.2 (Connection-Config Provider component), §11 Phase 1 (replace "reverse-map the feed" with the staged plan). Status is "Approved design; ready for implementation planning" — but:

- `spec.md` §5.1/§5.2 contains no Connection-Config Provider. The component model still shows an "AVD Feed provider" with different responsibilities.
- `spec.md` §11 Phase 1 step 2 still reads "reverse-map the **AVD feed protocol**", the framing the design replaced.
- `spec.md` §6.2 still lists the AVD feed audience as an "**Open question** — token audience/scope for the feed service is undocumented for third parties", while the design doc names it concretely (`https://www.wvd.microsoft.com/.default`, first-party client `a85cf173-…`, AVD API `9cdead84-…`).

**Effect:** a reader who starts at `spec.md` — which README presents as the primary document — gets a materially staler and more pessimistic picture than the approved design. Two sources of truth, silently diverged.

**Recommendation:** apply the three edits, then add to the spec a "Superseded by" pointer for any section the design doc overrides. Adopt a rule: an approved design doc's impact list is applied as part of accepting it, not after.

### G-02 — Two unreconciled roadmap numbering schemes · **P0**

`spec.md` §11 has **Phase 0/1/2**. The design doc has **Stage 0/1/2/3/4**. They overlap partially (Stages 0–3 sit inside Phase 1; Phase 2 has no Stage; Stage 4 has no Phase) and are never mapped to each other. README uses Stages only. Both documents cross-reference the other scheme's numbers in body text (`spec.md` §8 "Phase 1 is original work"; design doc "roadmap Phase 1").

**Recommendation:** one scheme. Keep Phases as the *product* delivery axis (what ships to users) and Stages as the *native-path engineering* axis inside Phase 1, and publish an explicit mapping table:

| Phase | Contains | Ships to users |
| --- | --- | --- |
| 0 — Web-first | — | Yes |
| 1 — Native MVP | Stages 0, 1, 2, 3 | Yes (Stage 3 complete) |
| 1.5 — Upstream | Stage 4 | No (maintenance posture) |
| 2 — Shortpath | — | Optional |

### G-03 — The signing/trust model is self-contradictory · **P0** — *highest-severity single finding*

`spec.md` treats the connection config as a Microsoft-signed artifact to be passed through opaquely, in at least six places (lines 40, 52, 74, 105, 528, 538) and most consequentially in §10 Security: *"the `.rdpw` is Microsoft-signed; the client treats it as opaque and passes it to FreeRDP unmodified."*

The design doc states the opposite and builds on it: *"FreeRDP **ignores the `.rdpw` signature**, so a reconstructed file needs no re-signing"*, and Stage 2's deliverable is to **serialize** a `.rdpw` the app composes itself from parsed feed fields.

These cannot both be true, and the difference is not cosmetic — it relocates the entire trust anchor. If the app synthesizes the file and FreeRDP does not verify a signature, then the integrity of `full address` / `gatewayhostname` / `armpath` rests **solely** on TLS to `rdweb.wvd.microsoft.com` plus whatever validation the app itself performs. An attacker who can MITM the feed (corporate TLS-inspection proxy, compromised CA, a hostile DNS answer) can steer a native session to an arbitrary host, and §10 as written gives the reader no reason to look for that.

**Recommendation:**

- Rewrite §10's connection-config bullet to state the real model: the config is *composed by the client from feed data*; the signature is not a trust control because FreeRDP does not verify it.
- Add explicit compensating controls to §10: strict TLS validation with no user-bypass on feed calls; consider certificate/public-key pinning for `rdweb.wvd.microsoft.com`; **allowlist-validate** every host-shaped field returned by the feed against expected Microsoft domain suffixes before writing it into the `.rdpw`; reject configs whose `full address`/`gatewayhostname` fall outside that allowlist and surface it as a security error, not a connection error.
- Correct the §2.2 table row and the §5.2 diagram text so "signed" appears only where describing Microsoft's own documented behavior, not the app's dependency.

### G-04 — Broken documentation links after the `spec.md.txt` → `spec.md` rename · **P2**

Commit `1caf56c` renamed the spec but five references still point at the old name: `README.md:34` (the primary documentation link, now dead) and design doc lines 5, 90, 144, 205.

**Recommendation:** fix all five; add a CI link check (`lychee` or `markdown-link-check`) so doc renames fail loudly.

### G-05 — Design-doc status metadata is thin for a date-sensitive document · **P2**

"Status: Approved design" with no approver, no review date, and no next-review date, in a document whose factual base is explicitly stamped "Research (August 2026)" and whose subject changed materially in July 2026. `spec.md` has no version, changelog, or owner at all.

**Recommendation:** add front-matter to both (`version`, `owner`, `last-verified`, `next-review`), and a §14 changelog to the spec. See G-38 for the review cadence this feeds.

---

## 3. Feasibility of the critical path

### G-06 — Stage 1's capture dependency is unexamined and may be the real blocker · **P0**

Stage 1 says: capture the `feeddiscovery` → workspace-feed-download exchange, *"cross-check against the official Windows App / web client via a proxy such as mitmproxy."* Four problems, none addressed:

1. **Windows App does not run on Linux** — that is the premise of the project. The capture therefore requires a **Windows host** with Windows App ≥ 2.0.804.0 plus a subscribed tenant. This is an unlisted hard prerequisite on the critical path.
2. **TLS interception may be blocked.** If Windows App pins certificates or uses token/channel binding for the feed call, mitmproxy yields nothing. There is no stated fallback (e.g. Windows-side TLS key logging, or driving the feed from first principles with a Stage 0 token and iterating on 400-level responses).
3. **The web client may not exercise the same endpoint.** The spec itself notes `windows.cloud.microsoft` uses `/api/arm/weblaunch/...`, and the design doc's assumption #4 concludes `weblaunch` is *irrelevant to the native path*. So the web client is likely a **non-witness** for the feed contract, leaving Windows App as the only capture source — narrowing (1) rather than hedging it.
4. **Token/device binding.** If the feed token Windows App presents is bound to the Windows device (PRT, refresh-token binding), a token acquired by our own client on Linux may be rejected at the feed even when issuance succeeds. Stage 0's exit criterion (HTTP 200 from `feeddiscovery`) would catch this — but only if Stage 0 is executed *from Linux with the app's own token*, not with a replayed capture. State that explicitly.

**Recommendation:** rewrite Stage 1 with (a) an explicit prerequisites list including the Windows capture host, (b) a primary method and at least one stated fallback, (c) a note that the web client is not expected to be a valid witness, and (d) a timebox. Move capture feasibility into the unverified-assumptions list — it currently is not there.

### G-07 — Stage 0 has no abort branch, no timebox, and no cost ceiling · **P1**

Stage 0 is framed as the project's gate, but both branches lead to "proceed to Stage 1". There is no third outcome. Plausible third outcomes exist: the scope is grantable but requires admin consent no pilot tenant will give; first-party-ID reuse works from FreeRDP's process but is rejected for our redirect URI; the feed requires a device-bound token (G-06.4). None of these stop the project in the current text, which defeats the purpose of a gate.

**Recommendation:** add a **no-go branch** with a named consequence ("native path shelved; product ships web-only; revisit if FreeRDP upstream lands feed support independently"), a timebox in days, and the decision-maker's name. A gate without a stop condition is a milestone, not a gate.

### G-08 — Stage 4 (upstreaming) has an exit criterion the project cannot unilaterally satisfy · **P1**

"An upstream PR (or accepted feature)" depends on FreeRDP maintainer acceptance, on their timeline, and on their appetite for code that calls an undocumented Microsoft endpoint — plausibly contentious. The design also makes Stage 4 the *mitigation* for the ToS risk of first-party-ID reuse, which puts the risk mitigation outside the project's control.

**Recommendation:** split into what the project controls (open the PR; maintain a downstream patch set; document the posture) and what it does not (acceptance). Do not let "upstreamed" be a precondition for shipping, and give the ToS risk a mitigation the project owns independently (see G-35).

### G-09 — "The routing blob is static" is load-bearing but unverified, and caching has no TTL · **P0**

The design's core simplification is that the `.rdpw` is *"mostly static per-resource routing metadata"* and therefore cacheable. Stage 2 says "Cache the *static* routing blob per resource; re-fetch on demand and on feed/auth errors." But:

- `spec.md` §8 records Microsoft's own note that the file *"contains a `loadbalanceinfo` routing token"* — token-shaped, not obviously static.
- `spec.md` §9 has an error row *"if the signed config has expired, re-acquire before reconnecting"* — i.e. the spec already assumes configs **do** expire, contradicting the design's staticness premise.
- No TTL, no invalidation triggers, no staleness detection, and no persistence rules are specified for the cache.

If any field is short-lived, per-resource caching produces intermittent, hard-to-diagnose connection failures — the worst failure class for a desktop client.

**Recommendation:** promote this to unverified assumption #5 with an explicit Stage 1 test ("re-fetch the same resource at T+0 / +1 h / +24 h and diff the fields"). Specify the cache contract in §5.2: what is cached, where, TTL, invalidation triggers (auth change, resource state change, connection failure with a gateway-rejection code), and whether it persists across app restarts. Default to **no persistence and re-fetch per launch** until staticness is proven — it costs one HTTP round trip and removes a whole failure class.

### G-10 — Library-linking vs subprocess is an unresolved architectural fork · **P0**

`spec.md` §5.1 says *"thin wrapper around **libfreerdp**"*; §5.2 says *"hand it to **libfreerdp** with the ARM-gateway/AAD options"*; the design doc Stage 3 says *"**invoke FreeRDP** 3.30.0+ with the generated `.rdpw` and `/gateway:type:arm /sec:aad`"*; the §2.2 FAQ pattern is a CLI invocation. These are materially different architectures:

| | Subprocess (`xfreerdp` / `sdl-freerdp`) | Linked `libfreerdp` |
| --- | --- | --- |
| Language freedom | Any | C/C++/Rust realistically |
| Session window | FreeRDP's own; host app can't embed or tab it | Embeddable, unified UX |
| Error surfacing | Exit codes + stderr parsing (brittle) | Structured callbacks |
| Config handoff | File on disk (0600, deleted after start) | In-memory possible |
| ABI/version risk | Low — CLI flags are comparatively stable | High — libfreerdp API churns across 3.x |
| Packaging | Bundle the binary | Bundle and link the sonames |

**Recommendation:** decide now, because it constrains G-11 (language) and G-18 (session UX). Recommended: **subprocess for Phase 1** — the flags are the documented upstream contract, it keeps the language choice open, and it isolates crashes; revisit linking only if embedded session windows become a requirement. Whichever is chosen, purge the other's vocabulary from both documents.

### G-11 — "Implementation-stack-agnostic" becomes a blocker at Stage 2 · **P1**

§1 declares no language/toolkit is committed, and §6.1 lists MSAL flavors as "non-binding". That is right for §1–§10, but Stage 2 writes product code and cannot stay agnostic. The choice is constrained by four things the spec already requires: MSAL availability, keyring (libsecret/KWallet) bindings, FreeRDP integration mode (G-10), and Linux packaging.

**Recommendation:** add a **Stage 1.5 stack-decision gate** with a written decision record and criteria, evaluated against: MSAL support quality on Linux, secret-service bindings, Flatpak packaging maturity, FreeRDP integration mode, and contributor availability. Note in the record which of §1's abstractions become concrete.

### G-12 — §8 gives one endpoint for a two-stage protocol · **P1**

§8 names `https://rdweb.wvd.microsoft.com/api/arm/feeddiscovery` and describes the exchange as two stages — *"initial feed discovery"* and *"workspace feed download"* — but never names the second endpoint, its inputs, or where its URL comes from (presumably the discovery response). The `.rdpw` fields the project needs live in stage two.

**Recommendation:** state explicitly that the stage-two URL is expected to come from the discovery response, and make "confirm the two-stage chain and capture both" an explicit Stage 1 deliverable alongside the field-mapping table.

### G-13 — No stopped / deallocated Cloud PC story · **P1**

Stage 2 mentions handling `E_PROXY_ORCHESTRATION_LB_SESSIONHOST_DEALLOCATED`, but §7.1's endpoint matrix shows **no start/resume action on any path**, `/me` or admin. Windows 365 Frontline and Flex Cloud PCs deallocate; a user clicking Connect on a stopped Cloud PC is a routine first-run experience.

**Recommendation:** specify the behavior end to end — whether connecting triggers implicit start (which is what that error code implies), how long the app waits, what the UI shows during the wait, and the timeout. Also enumerate which `status` values permit Connect at all (G-19).

### G-14 — ANC / private-network Cloud PCs are mentioned and then dropped · **P2**

§2.1 notes Azure Network Connection / private Shortpath over UDP/3390 exists, then the spec never returns to it. For ANC Cloud PCs the session host may be unreachable from an arbitrary Linux host's network, and private-Shortpath-only configurations would fail on a TCP-only client.

**Recommendation:** state the support position explicitly — supported / best-effort / out of scope — and add an error row for "Cloud PC on a private network unreachable from this host".

---

## 4. Missing functional scope

The spec has five functional requirements, all control-plane. A client positioned as a Windows App analogue needs the following, none of which appears anywhere in the document set. Each is a *specification* gap, and several are *feasibility* gaps too.

### G-15 — Device and resource redirection is entirely absent · **P0 for spec, P1 for delivery**

No mention of clipboard (text / image / file), drive or folder redirection, printers, audio out, microphone, cameras, smartcards, USB, or serial. FreeRDP supports most of these behind flags, so the work is largely policy and UI — but that is exactly what a spec should decide, and Wayland and Flatpak both complicate it. Also relevant: AVD/W365 **host-side RDP properties may disable** redirections the client requests, so client-side settings are requests, not guarantees — the UI has to reflect that.

**Recommendation:** add a redirection matrix as a new §5.5: capability × default state × FreeRDP flag × per-resource-overridable? × Wayland/Flatpak caveat × host-policy-can-override?. Suggested Phase-1 defaults: clipboard (text) on, audio out on, drive redirection off by default with an explicit folder picker, printers / smartcards / USB / camera deferred with a stated rationale.

### G-16 — Display: no multi-monitor, resolution, dynamic resize, or HiDPI specification · **P1**

Nothing on monitor selection, span, per-monitor DPI, fractional scaling, dynamic resolution on window resize, or fullscreen behavior. On mixed-DPI Linux desktops this is the most common source of "the client is broken" reports.

**Recommendation:** add a §5.6 display section with explicit Phase-1 scope (recommended: single monitor, windowed plus fullscreen, dynamic resolution if the chosen FreeRDP client supports it, integer scaling only) and a stated deferral for multi-monitor span.

### G-17 — X11 vs Wayland is never named — a genuine feasibility gap · **P0**

The spec targets "Linux desktop" and names libsecret/KWallet, but never states the display-server support matrix. This matters more than it looks: keyboard grab and hotkey passthrough, clipboard integration, global shortcut capture, cursor confinement, and multi-monitor enumeration all behave differently (or are restricted) under Wayland, and FreeRDP 3's client backends do not all treat Wayland as a first-class target — sessions commonly run through XWayland with the attendant scaling and grab caveats.

**Recommendation:** state the matrix (e.g. "X11 supported; Wayland supported via XWayland with limitations documented in §5.6; native Wayland tracked as future work"), and verify the current FreeRDP 3.30.x client-backend situation as a named Stage 3 task rather than an assumption. This is the kind of gap that surfaces as a late, expensive UX surprise.

### G-18 — Session lifecycle and window management unspecified · **P1**

No statement on: disconnect vs sign-out; whether multiple simultaneous sessions are allowed and how they are presented (separate windows, tabs, a session list); reconnect-on-drop policy and retry limits; idle/timeout handling; what happens when the app quits with a live session; keyboard-layout and IME mapping to the remote host. §9 has a single row: "Standard reconnect prompt".

**Recommendation:** add a §5.7 session-lifecycle section covering the above, plus a per-resource "already connected" rule. Note that §5.3's *"launching a second RemoteApp tab from the same host pool disconnects the first"* caveat has a native-path analogue that needs its own answer.

### G-19 — Cloud PC status values and the action-availability matrix are undefined · **P1**

§4.1 renders a status chip and §4.3 offers four actions, but nowhere is the `status` value set enumerated, nor which actions and which Connect methods are legal in each state. Reprovision on a provisioning Cloud PC, Connect on a failed one, Rename during resize — all undefined. Grace-period Cloud PCs get an admin-only `endGracePeriod` row in §7.1 with no end-user UI consequence stated.

**Recommendation:** add a status × (Connect native / Connect web / Restart / Rename / Troubleshoot / Reprovision) enablement matrix, sourced from the Graph `cloudPC` resource's documented status enum, with the tooltip text for each disabled cell.

### G-20 — Admin-capability detection has no mechanism · **P1**

§4.3 and §7.1 gate Restore and Resize on the account being *"demonstrably admin-capable"*, and §12.3 repeats the requirement — but no detection method is specified. The candidates (token role/`wids` claims; probing the `virtualEndpoint` endpoint and treating 403 as "not admin"; `/me/memberOf`) have very different UX and failure modes, and probe-based detection means a 403 on every startup.

**Recommendation:** pick one and specify it, including the failure mode (unknown → hide, never show-and-fail) and per-account caching.

### G-21 — Product-scope boundary across Windows 365 SKUs and Dev Box is undrawn · **P1**

§5.3 mentions "Enterprise and Flex Dedicated" for direct-launch URLs. Never stated: whether Windows 365 **Business**, **Frontline** (shared and dedicated), and **Microsoft Dev Box** are in or out of scope. Frontline shared has different connect semantics, Business licensing may behave differently through `/me/cloudPCs`, and Dev Box uses a separate devcenter API surface entirely.

**Recommendation:** add an explicit in-scope / out-of-scope SKU table to §1, marking which entries require verification against a real tenant.

### G-22 — Phase 0's web path has no browser support matrix or limitation disclosure · **P1**

Phase 0 is the thing that ships first and it delegates the whole session to a browser. Unstated: which Linux browsers are supported; what the web client cannot do (local drive redirection, most peripheral redirection, some audio paths); how the app behaves when `xdg-open` resolves to something unexpected; and what happens under Flatpak sandboxing, where opening a URL goes through a portal.

**Recommendation:** add a browser matrix and a plain-language "what the web path can't do" list to §5.3. This is also the honest justification for building the native path at all, so it strengthens the document.

### G-23 — No offline-cache specification, despite the UI depending on it · **P1**

§4.4's Offline state shows *"cached resource list greyed"*, but nothing specifies where that cache lives, what it contains (resource display names and IDs are tenant-identifying), whether it is encrypted, its retention, or whether it is cleared on sign-out. §10 covers tokens only.

**Recommendation:** specify the resource cache alongside the token cache in §6.3 and §10: location (XDG state dir), contents, encryption posture, clearing on account sign-out, and a TTL beyond which the list is too stale to show.

### G-24 — No update, telemetry, diagnostics, or support-bundle story · **P2**

For a client whose critical path depends on an undocumented endpoint that *will* change, field diagnosability is a functional requirement, not an afterthought. Nothing on version checking or update prompts, crash reporting, an opt-in diagnostics bundle, or how a user reports "Connect (Native) stopped working" usefully.

**Recommendation:** add a diagnostics section under §10: a user-triggerable support bundle with the redaction rules of §10 already applied; a feed-contract-mismatch log signature the project can search for in issue reports; and a stated telemetry position (recommended: no telemetry, opt-in bundles only — also the easiest privacy posture to defend).

### G-25 — Accessibility and localization absent · **P2**

No keyboard-navigation requirement, screen-reader / AT-SPI expectations, contrast guidance, or i18n/l10n plan — and §4's UI text is hardcoded English throughout.

**Recommendation:** a short §4.5: keyboard-navigable throughout, AT-SPI labels on all controls, all user-visible strings externalized from day one (cheap now, expensive later), localization deferred with the mechanism in place.

---

## 5. Identity and Conditional Access

### G-26 — Conditional Access device compliance is the largest unexamined feasibility risk · **P0**

The spec handles CAE claims challenges well (§6.4, §12.8) but never addresses **device-based** Conditional Access. Many Windows 365 tenants — the exact population that would want this client — gate Cloud PC access on policies such as *require compliant device*, *require hybrid Entra joined device*, *require approved client app*, or *require token protection*. An unbrokered public-client app on an unregistered Linux desktop satisfies none of those. The user would sign in successfully and then be blocked, and no amount of feed reverse-engineering fixes it.

On Linux, device registration and device-bound SSO/PRT go through Microsoft's Linux identity broker component together with Intune's Linux support, on a limited distro matrix. Using it is a substantial architectural commitment (broker IPC, a supported-distro list, an enrollment prerequisite); *not* using it caps the addressable tenant population, possibly severely.

**Recommendation:** add a §6.5 Conditional Access section that (a) enumerates the policy types that would block the client, (b) states the Phase-0 position honestly ("tenants enforcing device-based CA are not supported; the app must detect that failure and say so, not show a generic sign-in error"), (c) adds a specific error row mapping CA-block error codes to actionable text, and (d) records broker integration as an evaluated-and-deferred decision with the distro-matrix cost noted. Then add a **pre-Stage-0 question**: does the pilot tenant enforce device-based CA? If it does, Stage 0's result is uninterpretable.

### G-27 — Loopback redirect registration details are one sentence short of actionable · **P1**

§6.1 and §12.6 correctly and valuably flag the `spa`-vs-native redirect trap (24 h refresh-token cap). But the mechanics are unspecified: fixed port vs ephemeral, what exactly to register, behavior when the port is taken, and loopback-listener hardening (bind `127.0.0.1` only, `state` validation, single-use, timeout).

**Recommendation:** specify ephemeral-port loopback with the exact registered redirect form, and verify-and-record Entra's current port-matching behavior for loopback native redirects as a Stage 0 sub-task. Add the listener hardening requirements to §10.

### G-28 — No headless / no-browser / SSH fallback · **P2**

§6.1 assumes a system browser or embedded webview. A Linux user on a minimal desktop, a remote X session, or a kiosk may have neither usefully available. Device code flow is the standard answer and costs little.

**Recommendation:** specify device code flow as a documented fallback, noting that it does not satisfy device-based CA either (G-26).

### G-29 — Keyring-unavailable path is described but not fully specified · **P2**

§6.3 says the fallback is *"an encrypted file with a documented, weaker guarantee"* plus a warning — but encrypted with what key? A passphrase-derived key means prompting on every launch; a key stored beside the ciphertext is obfuscation, not encryption, and saying so matters. Flatpak adds a wrinkle: secret-service access goes through a portal that may be unavailable.

**Recommendation:** specify the fallback concretely (recommended: passphrase-derived with a strong KDF, prompted per session, with the UX cost accepted and stated) or drop the fallback and require a keyring. Add the keyring-unavailable row to §9.

### G-30 — Multi-account edge cases underspecified · **P2**

FR-3 is otherwise thorough, but: the same human in two tenants (guest / B2B) — one account or two? Do live sessions survive switching, *and* sign-out of the owning account? Can two accounts hold sessions to two Cloud PCs simultaneously? What does account switching do to an in-flight enumeration or a pending action?

**Recommendation:** add three or four sentences to FR-3 covering identity keying (home account ID, which the spec already names — make it normative), session ownership on sign-out, and in-flight-operation cancellation.

---

## 6. Non-functional requirements

### G-31 — There are no non-functional requirements at all · **P0**

The spec has no NFR section. Absent entirely: session-establishment time budget, enumeration latency target, app startup time, memory/CPU ceilings, minimum supported distro and glibc versions, supported desktop environments, minimum FreeRDP version enforcement *at runtime*, the network conditions under which TCP-only is deemed acceptable, and maximum simultaneous sessions.

The last two are decision-relevant, not cosmetic: Phase 1's own gate is *"**if TCP-only performance is acceptable**"* — a conditional the spec never makes measurable. Without a number, that gate cannot be passed or failed.

**Recommendation:** add a §5.8 NFR section with at least: cold-start-to-session-visible target; enumeration p95; a TCP-only acceptability criterion expressed as a measurable test (e.g. "subjectively usable for office workloads at ≤ 80 ms RTT and ≤ 1 % loss, measured with a stated method on a stated workload"); the supported distro/DE matrix; and runtime FreeRDP version detection with a clear error when older than 3.30.0 (G-32).

### G-32 — The FreeRDP 3.30.0+ requirement has a packaging consequence the spec never draws · **P0**

§2.2 says *"Build against FreeRDP 3.30.0 or newer (released July 16, 2026), **not whatever older version a distribution ships**"* — and then the spec never returns to it. But that sentence is a packaging mandate: on most distributions, for a long while, the system FreeRDP will be older than 3.30. Consequences: the app must **bundle** FreeRDP (Flatpak / Snap / AppImage, or a vendored build), or refuse to enable the native path, or both. Bundling interacts with G-15 (redirection needs device and portal access from inside a sandbox), G-29 (keyring via portal), and G-10 (bundle a binary vs link sonames).

**Recommendation:** make the version floor a normative requirement with a runtime check and a specified error state, and resolve it jointly with G-33.

### G-33 — No packaging and distribution plan · **P0**

Nothing on distribution format, sandboxing, or how users install this. For a Linux client this is a primary design axis, not an afterthought, and it is entangled with nearly every gap above.

**Recommendation:** add a §5.9 packaging section. Recommended primary: **Flatpak** — it solves FreeRDP bundling and gives a plausible cross-distro story — with the sandbox costs enumerated explicitly: secret-service portal for the keyring, `xdg-open` portal for the web path, filesystem portal for drive redirection, device access for audio / camera / smartcard, and USB redirection likely infeasible. Add a distro-package (deb/rpm) path as secondary, with the FreeRDP version floor stated as a hard dependency. Verify the bundling-vs-portal costs early — several of them can quietly delete features from §5.5.

---

## 7. Security, legal, and licensing

### G-34 — §10 is a control list, not a threat model · **P1**

The controls in §10 are individually sound: keyring storage, PKCE, no embedded secret, 0600 short-lived config files, redaction rules, and the honest `#loginHint` browser-history trade-off. What's missing is the adversary model that tells a reviewer whether the list is *sufficient*. Unaddressed threats: MITM on the feed (G-03); a malicious or compromised local process reading the 0600 config or the loopback redirect; another local app racing the loopback listener port; log and support-bundle exfiltration; and a hostile `.rdpw` field injecting FreeRDP options — **argument injection is a real concern in the subprocess design of G-10**, since feed-sourced strings would flow into an argument vector.

**Recommendation:** add a short threat model to §10 (assets, adversaries, trust boundaries) and add the injection control explicitly: feed-sourced values are written into a config file with strict per-field validation and type checking, never concatenated into a command line.

### G-35 — ToS and legal posture has no owner, no gate, and a dependent mitigation · **P0**

Two distinct legal exposures are named but not managed:

1. **Reusing the first-party AVD client ID** (`a85cf173-…`) — the mitigation given is "upstreaming makes it defensible", which is (a) an argument, not a legal opinion, and (b) outside the project's control (G-08).
2. **Traffic capture against Microsoft services** to reverse-map the feed (Stage 1) — service terms commonly restrict this, and it is the *method* on which the whole native path depends. The design doc does not mention this exposure at all; `spec.md` §12.4 gestures at "support/ToS risk" generally.

**Recommendation:** insert an explicit **legal-review checkpoint between Stage 0 and Stage 1**, with a named owner and a written position on both exposures, plus the recorded fallback if the position is negative (web-only product, or wait for upstream). Also state which tenant the capture is performed against — capture in a tenant the project controls is a materially different posture than capture in a customer tenant, and the tenant owner's consent should be documented.

### G-36 — No LICENSE file, and no license-compatibility analysis · **P1**

The repository has no `LICENSE`. FreeRDP is Apache-2.0; the integration mode chosen in G-10 determines whether the project merely *invokes* it (no linking obligations) or *links* it (Apache-2.0 obligations, plus attention to any GPL/LGPL dependencies pulled in transitively, e.g. codec or crypto libraries). Stage 4 also requires contributing code upstream, which requires the project's own license to be compatible with FreeRDP's contribution terms.

**Recommendation:** add a `LICENSE` (Apache-2.0 is the natural choice given the Stage 4 goal), plus a short dependency-license table once the stack is chosen (G-11).

### G-37 — No privacy statement for a product that handles identity and tenant data · **P2**

The app holds UPNs, tenant IDs, resource names, and tokens. §10 has redaction rules, but there is no user-facing statement of what is stored, where, and for how long, nor a GDPR-shaped position — needed for a distributable product, and cheap to write given §6.3 and §10 already contain the facts.

---

## 8. Specification hygiene and process

### G-38 — Date-sensitive facts have no review cadence, and one deadline is ~7 weeks out · **P1**

The spec rests on volatile, explicitly dated claims: "re-verified August 2026"; "as of July 2026 the web client redirects…"; "`getCloudPcLaunchInfo` stops returning data **October 30, 2026**"; "FreeRDP 3.30.0 released July 16, 2026". As of this assessment (2026-09-12) the October 30 hard stop is roughly seven weeks away. The spec correctly says never to depend on the deprecated call — good — but there is no mechanism to catch the *next* such change, which the risk register itself (§12.7, "URL/API churn") says to expect.

**Recommendation:** add a volatile-facts register to §12 with columns: fact · source · last verified · next review · owner · what breaks if it changes. Review before each phase boundary at minimum, and re-verify every post-August-2026 claim at the start of Stage 0, since several of them gate the plan.

### G-39 — FR-1…FR-5 have no acceptance criteria · **P0**

The functional requirements are prose. None is written so that a tester can pass or fail it, and §11 maps FRs to phases with soft language ("FR-1 partial", "FR-2 web-only") that does not define done. The design doc's Stage exit criteria are much better in this respect — that quality should be pulled back into the FRs.

**Recommendation:** give each FR numbered, testable acceptance criteria (`FR-1-AC-1`, …) and a traceability matrix: FR → AC → Phase/Stage → verification method (unit / integration against a live tenant / manual). This single addition does more for hand-off readiness than any other item on this list.

### G-40 — No test strategy and, critically, no test-environment prerequisites · **P0**

For a project whose critical path is empirical, this is the most schedule-relevant omission in the set. Nowhere is it stated what is needed to do the work at all:

- A tenant with Windows 365 Enterprise licenses and at least one provisioned Cloud PC.
- An AVD host pool with a published desktop and a RemoteApp, and a workspace.
- The ability to **grant tenant admin consent** — both CloudPC scopes require it (§6.2).
- A second tenant, or a guest account, to exercise multi-account and `?tenant=` behavior.
- An admin-capable account *and* a plain end-user account, to test G-20 both ways.
- A tenant **without** device-based Conditional Access, and ideally one with, to characterize G-26.
- A **Windows host with Windows App** for the Stage 1 capture (G-06).
- A Linux test matrix: distros × X11/Wayland × desktop environments (G-17).
- Fixtures: a captured feed response and a known-good `.rdpw` for offline parser tests. The design doc's Stage 1 verification implies these — make them first-class checked-in artifacts.

Also missing: the mock/fixture strategy for Graph and the feed, how CI tests anything that requires a live tenant, and a manual test plan for the native session.

**Recommendation:** add a §13 test strategy with the prerequisites checklist as its first subsection, and treat procurement of that environment as **Stage -1** — work that precedes the Stage 0 gate. Design the provider (G-09) so its parser is testable purely from fixtures; that is the only part of the native path that can be covered in CI.

### G-41 — Terminology drift: `.rdpw` vs `.rdp`, "feed" overloaded · **P2**

`.rdpw` and `.rdp` are used interchangeably (§5.2 writes `.rdpw/.rdp`) and never distinguished. "Feed" denotes the discovery endpoint, the workspace download, the AVD provider component, and the protocol as a whole, in different sentences.

**Recommendation:** add a glossary defining `.rdp`, `.rdpw`, feed discovery, workspace feed, connection config, Reverse Connect, Shortpath, ARM gateway, and weblaunch — then use the terms consistently.

### G-42 — Graph integration details thin for a section titled "endpoint matrix" · **P2**

§7.1 is a good matrix but omits: pagination (`@odata.nextLink`) on `/me/cloudPCs`; `$select` usage; response-shape expectations; the distinction between 401 (token) and 403 (consent/permission) in §9; and the long-running nature of the action endpoints — does `POST .../reprovision` return 202 with something to poll, or does the app rely solely on §7.3's 60 s status poll?

**Recommendation:** make pagination mandatory, split 401/403 handling in §9, and state the action-completion model — poll-based status observation is fine, but say so and give the timeout.

### G-43 — §4's ASCII mock-ups are the only UI source of truth · **P2**

Charming and clear, but there is no state inventory per component, no error-text catalogue, and no empty/loading/disabled variants beyond §4.4's prose. The disabled-reason strings in FR-2 and §4.4's messages are scattered across three sections.

**Recommendation:** consolidate all user-visible strings into one table (string ID · text · trigger condition). It also serves G-25's externalization requirement and G-19's tooltip needs.

---

## 8A. Second pass — gaps found after the P0 edits

Committing to a subprocess launcher, Flatpak packaging, NFR budgets and a threat model resolved the big questions and opened sharper ones. G-44 to G-47 are gaps the P0 pass *created*; G-48 to G-54 are implementation concerns the first pass missed. All are tracked in the Implementation Linear project; G-45, G-47 and G-48 were promoted to P0 and are already applied to `spec.md`.

### Created by the P0 pass

**G-44 — The subprocess decision has no exit-code taxonomy · P1**
§9 demands distinguishable failure reasons; §5.1.1 then chose a subprocess whose only structured output is an exit code plus stderr. No mapping exists between them, so **§9 is currently unimplementable for the native path**. This is also where §5.1.1's own revisit trigger gets evaluated — if exit codes cannot distinguish the §9 rows, the subprocess decision reopens. Do it early, not at Stage 3.

**G-45 — §5.6 and §5.8 were circularly dependent · P0, applied**
§5.6 deferred the FreeRDP backend choice to Stage 3; §5.8 needs a named binary in the Flatpak manifest. Packaging was blocked on a decision the display section had pushed past it. *Applied:* the decision moved to Gate STACK, the behavioral verification stayed at Stage 3.

**G-46 — NFR-3's budget fights §5.2's no-persistence rule · P1**
NFR-3 allows ≤ 3 s for config acquisition; §5.2 mandates re-fetch per launch, which is three or four sequential Microsoft round trips. Resolution is one of: widen the budget, permit in-session caching after the first launch (already allowed — the cache is per-process), or make Stage 1's staticness test a performance decision too.

**G-47 — Strict TLS could break the app for its own target users · P0, applied**
§10.1 required strict TLS and floated pinning. But enterprises running Windows 365 routinely TLS-inspect egress with a corporate CA, which a pinned client cannot distinguish from an attack — so pinning fails closed in exactly the deployments that matter. *Applied:* pinning **rejected** rather than deferred, validation via the system trust store, integrity resting on host allowlisting and field validation, and the residual (an adversary holding an already-trusted CA) moved explicitly out of scope in §10.8. The same finding surfaced that **proxy support was absent entirely** — now §5.9, covering all three egress paths and the fact that the FreeRDP subprocess does not inherit the client's proxy configuration.

### Missed by the first pass

**G-48 — No single-flight or cross-process story for token refresh · P0, applied**
FR-4-AC-1 requires acquisition before every call; §6.3 rotates refresh tokens on every use. Concurrent acquisitions can each redeem the current RT and strand one another — a spurious `ReauthRequired` from nothing worse than a refresh and a status poll colliding. Two processes sharing the keyring-backed cache race identically. *Applied:* §6.3 single-flight subsection, serialized writes, single-instance enforcement, clock-skew margin, FR-4-AC-6/AC-7, and MSAL behavior added to Gate STACK. **The only latent correctness bug in the set, not a documentation gap.**

**G-49 — No persistence model or schema migration · P1**
Five separate stores implied across the spec, with no statement of location, format, or migration. Version-0 persistence without a migration path is what forces a "sign in again and lose your settings" release.

**G-50 — No concurrency model · P1**
Polling, per-call acquisition, dual-provider enumeration, actions, and N subprocess supervisors run concurrently with no stated model or cancellation semantics — though FR-3-AC-2 already *requires* cancellation.

**G-51 — The subprocess session has no UX contract · P1**
Unspecified: the wait before concluding the session window never appeared; **what happens to live sessions when the client quits** (subprocesses outlive it by default); navigation back to the client; taskbar relationship; and how "visible session window" is detected — which NFR-3 measures, making that NFR unmeasurable until defined.

**G-52 — Reprovision versus a live session · P1**
§7.4 gates on Graph `status`, which knows nothing about our own session. Reprovisioning a Cloud PC you are connected to is undefined, and the confirmation dialog should name it.

**G-53 — Multi-tenant app registration has no owner or lifecycle · P1**
Who owns the registration, publisher verification, rotation response (the client ID is in every binary), and the fact that the two Stage 0 identity outcomes lead to materially different distribution postures — one where we own an asset, one where there is nothing to own and the ToS exposure is live instead.

**G-54 — Graph correlation IDs aren't captured · P2**
`request-id` / `client-request-id` are how Microsoft support investigates anything. §10.7 says what to redact and never what to record.

---

## 9. Prioritized recommendations

**P0 — before any product code (blocks or invalidates downstream work)**

| # | Action | Finding |
| --- | --- | --- |
| R1 | Apply the design doc's spec-edit list; reconcile the two roadmaps into one scheme with a mapping table | G-01, G-02 |
| R2 | Fix the signing/trust contradiction and add compensating controls (TLS strictness, host allowlisting, no argument injection) | G-03, G-34 |
| R3 | Stand up the test environment and write the prerequisites checklist as **Stage -1** | G-40 |
| R4 | Answer the Conditional Access question for the pilot tenant *before* Stage 0 — its result is uninterpretable otherwise | G-26 |
| R5 | Rewrite Stage 1 with prerequisites, a fallback capture method, and a timebox; add capture feasibility to the unverified assumptions | G-06 |
| R6 | Decide subprocess vs linked libfreerdp; purge the other vocabulary | G-10 |
| R7 | Promote "the routing blob is static" to a tested assumption; specify the cache contract; default to no persistence | G-09 |
| R8 | Add acceptance criteria to FR-1…FR-5 plus an FR → AC → Phase → verification traceability matrix | G-39 |
| R9 | Add a packaging section (Flatpak primary) and draw the FreeRDP-bundling consequence explicitly | G-32, G-33 |
| R10 | Add an NFR section, including a *measurable* TCP-only acceptability criterion | G-31 |
| R11 | Insert the legal-review checkpoint between Stage 0 and Stage 1, with a named owner | G-35 |
| R12 | State the X11 / Wayland support matrix | G-17 |
| R13 | Add a redirection matrix (§5.5) with Phase-1 defaults | G-15 |

**P1 — before the corresponding phase ships**

R14 Stage 0 no-go branch and timebox (G-07) · R15 split Stage 4 into controllable and uncontrollable halves (G-08) · R16 stack-decision gate at Stage 1.5 (G-11) · R17 status × action enablement matrix (G-19) · R18 admin-capability detection mechanism (G-20) · R19 session-lifecycle section (G-18) · R20 display/HiDPI section (G-16) · R21 stopped-Cloud-PC connect flow (G-13) · R22 SKU scope table (G-21) · R23 browser matrix and web-path limitations (G-22) · R24 offline resource-cache spec (G-23) · R25 loopback redirect mechanics and listener hardening (G-27) · R26 §8 stage-two feed endpoint (G-12) · R27 threat model (G-34) · R28 LICENSE and dependency-license table (G-36) · R29 volatile-facts register with review cadence (G-38)

**P2 — hygiene**

R30 fix the five `spec.md.txt` links and add a CI link check (G-04) · R31 doc front-matter and changelog (G-05) · R32 glossary (G-41) · R33 diagnostics and support-bundle section (G-24) · R34 accessibility and string externalization (G-25) · R35 device-code fallback (G-28) · R36 keyring-fallback key derivation (G-29) · R37 multi-account edge cases (G-30) · R38 Graph pagination, 401/403 split, action-completion model (G-42) · R39 consolidated UI string table (G-43) · R40 ANC / private-network position (G-14) · R41 privacy statement (G-37)

---

## 10. Structure of `spec.md` — as applied, and what remains

`✓` = landed in the P0 pass. Section numbers are the **actual** ones now in `spec.md`, not the originally proposed
ones: NFRs and packaging took §5.7/§5.8 rather than §5.8/§5.9, leaving session lifecycle to slot in later.

```text
1  Introduction and scope            ✓ AC pointer + committed-decisions note · todo: SKU table (G-21)
2  Background and constraints        ✓ signature-behavior note (G-03)
3  Functional requirements           ✓ FR-1…FR-5 acceptance criteria (G-39)
4  User interface                    todo: accessibility / i18n (G-25), UI string table (G-43)
5  Architecture                      ✓ 5.1 Connection-Config Provider (G-01)
                                     ✓ 5.1.1 FreeRDP integration decision record (G-10)
                                     ✓ 5.2 composed-config + cache contract (G-03, G-09)
                                     ✓ 5.5 redirection matrix (G-15)
                                     ✓ 5.6 display + X11/Wayland matrix (G-16, G-17)
                                     ✓ 5.7 NFRs incl. the NFR-6 measurable gate (G-31)
                                     ✓ 5.8 packaging — Flatpak primary + portal costs (G-32, G-33)
                                     todo: session lifecycle (G-18)
6  Identity and session lifecycle    ✓ 6.2 feed audience resolved (G-01)
                                     ✓ 6.5 Conditional Access and device state (G-26)
                                     todo: redirect/listener mechanics beyond 10.3 (G-27)
7  Microsoft Graph integration       ✓ 7.4 state-gated availability + deallocated case (G-13)
                                     todo: full status × action matrix (G-19), admin
                                           detection (G-20), pagination/async detail (G-42)
8  AVD feed integration              ✓ reframed to one call chain; two-stage note (G-01, G-12)
9  Error handling                    ✓ CA block, feed 401, validation failure, FreeRDP floor,
                                       deallocated, private network
                                     todo: keyring absent (G-29), 401/403 split (G-42)
10 Security considerations           ✓ 10.1 trust model rewritten (G-03)
                                     ✓ 10.4 argument/process handling, 10.8 threat model (G-34)
                                     todo: diagnostics (G-24), privacy statement (G-37)
11 Roadmap                           ✓ 11.1 one scheme + Phase/Stage map + Gates CA/LG-1/STACK
                                     ✓ 11.2 traceability matrix (G-02, G-07, G-08, G-39)
12 Risks and open questions          ✓ 4a capture method, 10 CA, 11 trust anchor, 12 staticness,
                                       13 version floor, 14 re-verification (G-06, G-26, G-38)
                                     todo: volatile-facts register with owners/dates (G-38)
13 Test strategy                     ✓ new — 13.1 prerequisites first (G-40)
14 References                        ✓ renumbered
   Glossary (G-41) · Changelog (G-05)  todo
```

---

## 11. Scope and limits of this assessment

This is a documentary and structural assessment. Every finding is derived from the three files in this repository plus general knowledge of Entra ID, Microsoft Graph, AVD / Windows 365 connectivity, FreeRDP, and Linux desktop packaging.

**It did not re-verify the spec's external factual claims**, and several load-bearing ones postdate this assessor's knowledge cutoff — specifically: the July 2026 removal of the web-client `.rdpw` download; the August 2026 Microsoft Q&A answer; the FreeRDP 3.30.0 release date and contents; the current FreeRDP `arm.c` behavior, including the claim that it ignores the `.rdpw` signature (the single most important technical claim in the design doc); the exact first-party client ID and AVD scope values; and the October 30, 2026 `getCloudPcLaunchInfo` hard stop. G-38's register exists precisely to keep those honest, and re-verifying them should be the first task of Stage 0, since the plan's shape depends on them.

Where this document recommends a specific technical position (Flatpak, subprocess FreeRDP, no-persistence caching, Apache-2.0), that is a recommendation with stated reasoning, not a constraint — the reasoning is given so it can be argued with.
