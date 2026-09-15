# Project plan — the 5-working-day feasibility sprint

**Status:** planned, not started · **Owner:** Kevin Kaminski · **Linear project:** Sprint 1

**Normative sources:** `spec.md` §11.1 (roadmap and gates), §14 (decision register). Where this plan and `spec.md`
disagree, `spec.md` wins.

---

## 1. What this sprint is

Five working days that answer **one question**:

> Can a client we control obtain a usable AVD feed token and get an HTTP 200 from `feeddiscovery` — from Linux,
> with its own interactively acquired token?

Every downstream estimate in the project is unknowable until that is answered, which is why **D-15** scoped five days
rather than committing to a phase. Three days of Stage 0 (**D-14**), two days of walking skeleton on whichever branch
results, then a re-plan with the finding in hand.

**What this sprint is not.** It is not Phase 0. Multi-account (FR-3), the full token state machine (FR-4), management
actions (FR-5) and Flatpak packaging are all outside it. It is also not a native session — Stages 1–3 are not two days
of work, and Branch A does not reach one.

**The deliverable is the finding and the re-plan, not the code.**

---

## 2. Entry conditions — before day 1

The sprint does not start until **S-0** is fully green. These days are not counted in the five.

| Prerequisite | Why it blocks |
| --- | --- |
| Windows 365 Enterprise licence, Cloud PC provisioned, `GET /me/cloudPCs` returns it (E-1) | No resource to enumerate or connect to |
| Admin consent path available in the test tenant (E-3) | Both CloudPC delegated scopes require it; without it every result is ambiguous |
| Linux workstation with Python 3, MSAL Python, a working keyring, FreeRDP ≥ 3.30.0 | Stage 0 is tested *from Linux*, deliberately |
| **Gate CA answered** — does the pilot tenant enforce device-based Conditional Access? | If it does, **Stage 0's result cannot be interpreted** (§6.5). The single most important entry condition |
| **V3** — PyGObject NFR smoke test (~20 minutes) | Can fire D-16's revisit trigger. Cheaper to know before the skeleton is written than after |

---

## 3. The five days

| Day | Focus | Output |
| --- | --- | --- |
| **1 am** | **S-1** — re-verify the volatile facts (timeboxed to half a day) | Verified / refuted list; sprint reshaped if a load-bearing claim has moved |
| **1 pm** | **S-2** — register our own multi-tenant Entra public client | A client ID, its **home tenant recorded** (irreversible — D-19), recovery path documented |
| **2** | **S-3** — acquire a feed token and call `feeddiscovery` from Linux | An HTTP status code, and the `aud` claim actually issued |
| **3 am** | Last focused attempt — the fallback identity if the own registration failed | — |
| **3 pm** | **S-4** — write the finding and **call the gate** | One-page finding; branch chosen |
| **4–5** | **S-5** (Branch A) *or* **S-6** (Branch B) | A walking skeleton on the branch the finding selected |
| **5 pm** | **S-7** — sprint close and re-plan | A re-plan built on the finding — the sprint's actual deliverable |

**Day 3 is for concluding, not for one more attempt.** Per D-14, expiry without a usable feed token means outcome 3
applies automatically and the project commits to a web-only MVP. That is decided in advance precisely so it is not
relitigated on day 4 under sunk-cost pressure.

**Running in parallel, days 1–3: Gate LG-1** — the legal position on first-party client-ID reuse and on traffic
capture as the Stage 1 method. It blocks Branch A, so leaving it to day 4 stalls the sprint *even when the technical
result is good*.

---

## 4. The branch point — end of day 3

Stage 0 has three recorded outcomes (§11.1). The finding names which one occurred.

| Outcome | Meaning | Days 4–5 |
| --- | --- | --- |
| **1 — own registration works** | Our client ID gets a usable feed token | **Branch A** (S-5) |
| **2 — scope not grantable** | Fall back to reusing the first-party AVD client ID, as FreeRDP does; recorded as an explicit product risk revisited at Stage 4 | **Branch A** (S-5), if LG-1 has cleared |
| **3 — no-go** | Neither identity yields a usable feed token, or the token proves device-bound | **Branch B** (S-6); native path shelved |

### Branch A — capture host and first feed capture attempt (S-5)

Stand up the Windows capture host and make the first attempt at the feed exchange. **Blocked on LG-1.** Two days buys
the capture attempt and an honest read on whether the method works at all — not a documented schema, and not a
session.

### Branch B — web-only walking skeleton (S-6)

Single-account interactive sign-in (auth-code + PKCE, ephemeral loopback port per D-6), token handling,
`GET /me/cloudPCs`, and web launch. **Not a consolation prize:** the web path is the always-available fallback in
every version of the plan (§5.3), so this work is needed regardless of the outcome — Branch A only defers it.

---

## 5. Gates and owners

| Gate | When | Owner | A negative result means |
| --- | --- | --- | --- |
| **Gate CA** | Pre-sprint, in S-0 | Kevin Kaminski | Stage 0 cannot be interpreted; the sprint does not start |
| **Gate LG-1** | Parallel, days 1–3 | Kevin Kaminski (interim) | Branch A is blocked; it is **not worked around** |
| **Stage 0 gate** | Day 3 pm, in S-4 | Kevin Kaminski | Branch B, per D-14 |

*Interim* on LG-1 means the gate has an owner and can therefore be cleared — not that the position has been legally
reviewed. Escalate to qualified counsel before the project distributes a build or reuses the first-party client ID in
a shipped artifact.

---

## 6. Definition of done

The sprint is complete when all of the following exist:

- A **one-page Stage 0 finding** — which identity worked or that neither did, the exact scope and consent path,
  whether admin consent was required, the `aud` claim issued in each attempt, and verbatim error responses for every
  failed attempt.
- The **S-1 verification results**, recorded against the claims they confirm or refute.
- A **running walking skeleton** on the selected branch.
- **`spec.md` amended** — any decision the sprint produced entered in §14 with an ID, a rationale and a revisit
  trigger; any section the finding contradicts corrected rather than left to diverge.
- A **re-plan** that sizes what comes next with the finding in hand, and the three Linear projects updated to match.

---

## 7. Ways this sprint ends early — all legitimate

- **Gate CA shows device-based CA enforced** → S-0 does not clear and the sprint is deferred until an interpretable
  tenant exists.
- **S-1 refutes a load-bearing fact** (for example, FreeRDP now verifies the `.rdpw` signature) → re-plan on day 1
  rather than spending four more days on a plan whose premise has moved.
- **D-14 fires** → Branch B and a web-only MVP. Pre-committed, not a failure.
- **LG-1 returns a negative position** → web-only, or waiting on FreeRDP upstream.

A negative result is an outcome this plan pre-commits to, not a problem to engineer around.

---

## 8. Linear mapping

| ID | Issue | Priority |
| --- | --- | --- |
| S-0 | Pre-sprint gate: prerequisites and Gate CA | Urgent |
| V3 | PyGObject NFR smoke test (pre-sprint) | High |
| S-1 | Day 1 am: re-verify the volatile facts | Urgent |
| S-2 | Day 1 pm: register our own Entra public client | Urgent |
| S-3 | Day 2: feed token and `feeddiscovery` from Linux | Urgent |
| S-4 | Day 3: write the finding and call the gate | Urgent |
| LG-1 | Legal position (parallel, days 1–3) | Urgent |
| S-5 | Days 4–5 Branch A: capture host | High |
| S-6 | Days 4–5 Branch B: web-only skeleton | High |
| S-7 | Sprint close and re-plan | Urgent |

Execution detail lives in the Linear issues. This document is the shape of the sprint; the issues are the work.
