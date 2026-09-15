# WinOnLinux — project overview

**Audience:** new team members, technical and non-technical. This is an introduction, not a specification. It explains
what we are building, why it is not straightforward, and what the first five days of work actually involve.

**Where to go next:** [`spec.md`](../spec.md) for the full specification, [`projectmanagement/projectplan.md`](../projectmanagement/projectplan.md)
for the sprint plan in detail.

---

## 1. The product in one paragraph

Microsoft sells **Windows 365 Cloud PCs** and **Azure Virtual Desktop** — a Windows desktop running in Microsoft's
cloud that you connect to from wherever you are. Microsoft ships a client app for that, called **Windows App**, on
Windows, macOS, iOS and Android. There is **no Linux version**. Linux users get a browser tab, which works but is a
second-class experience — no proper device redirection, no desktop integration, no window that behaves like an
application.

We are building the missing Linux client: sign in with your work account, see your Cloud PCs and virtual desktops in a
list, click one, and get a real desktop session.

---

## 2. Why this hasn't already been done

The honest answer is that one specific piece of the puzzle is missing, and it is missing for everyone.

**The connection is not a normal remote-desktop connection.** You cannot point a standard client at a Cloud PC,
because a Cloud PC has no publicly reachable address to point at. Instead the client authenticates with Microsoft,
receives a **connection configuration file**, and uses it to negotiate a connection through a Microsoft gateway that
then arranges for the cloud desktop to connect back the other way. Nothing works without that configuration file
first.

**The open-source remote-desktop engine already does almost all of this.** FreeRDP — mature, widely used, actively
maintained — implements the authentication, the gateway negotiation, the reverse connection and the desktop session
itself. It has done for some time.

**The gap is one step, at the very start.** FreeRDP expects you to already have the connection configuration file. It
never fetches one. The only place that file comes from is an **undocumented Microsoft endpoint** that Microsoft's own
clients call and that has no published contract, no SDK and no support commitment. Microsoft has stated publicly that
no public API for it exists.

So the project is not "build a remote desktop client." It is **"build the small missing piece that sits on top of an
existing, working one"** — fetch the configuration, hand it to FreeRDP, get out of the way. The long-term intent is to
contribute that piece back to FreeRDP so the whole Linux ecosystem benefits rather than just us.

That framing matters for expectations in both directions. The engineering is smaller than people assume. The
**uncertainty is larger**, because the one piece we need is the one piece nobody has documented.

---

## 3. What we are asking in the first five days

Everything in this project depends on a question we cannot answer from a desk:

> **Can an application we control get permission from Microsoft to read that connection configuration at all?**

Not "can we write the code" — we know how to write the code. The question is whether Microsoft's identity system will
issue our application the credential needed to make that call. It might. It might only do so for Microsoft's own
first-party applications. It might issue a credential that turns out to be locked to a Windows device, which would
make it useless to us on Linux.

Nobody on the team can answer this by reasoning about it. It has to be tried.

**The five-day sprint exists to try it, and to stop.** Three days to get an answer, two days to build the smallest
working thing on whichever answer we get, then re-plan with a real result in hand instead of an assumption.

---

## 4. Shape of the five days

| Day | What happens |
| --- | --- |
| **1, morning** | Re-check the handful of facts the plan rests on. Several are a year old in a space that keeps changing |
| **1, afternoon** | Register our application with Microsoft's identity system — one irreversible choice here, see below |
| **2** | The actual attempt: sign in, request the credential, make the call, record exactly what comes back |
| **3, morning** | One more focused attempt with the fallback approach, if the first did not work |
| **3, afternoon** | Write up the finding and **make the call** — which way the project goes |
| **4–5** | Build the smallest working thing on the branch that resulted |
| **5, afternoon** | Close out: what we learned, and a re-plan built on it |

Running alongside days 1–3, not after: a **written position on two legal questions** (see §6). It is easy to leave
this to the end and then discover it blocks day 4.

**Day 3 ends the investigation whether or not it succeeded.** This is written down in advance, deliberately, so that
on day 4 nobody has to argue against "we're so close, just one more day." A serious attempt that has produced no
result in three days has produced its answer.

---

## 5. The three possible outcomes — all of them are results

| Outcome | What it means | What happens next |
| --- | --- | --- |
| **Our own application is granted access** | The clean case. We ship under our own identity | Build the native client |
| **Only Microsoft's own application identity works** | Technically workable — it is what other open-source tools do — but it carries a real risk we would be taking on knowingly | Build, with that risk documented and escalated |
| **Neither works** | The native path is shelved | Ship a **web-based client** instead, and revisit if the situation changes |

The third outcome is the one worth talking about with a new team, because it is the one people instinctively treat as
failure. It is not. It is a five-day answer to a question that would otherwise sit unresolved behind months of
estimates. The project has **committed in advance** to accepting it rather than engineering around it, and the
web-based fallback is a genuinely useful product that we need to build anyway.

---

## 6. What we need in place before day 1

None of this is counted in the five days, and the sprint should not start until all of it is done.

| Prerequisite | Why it matters |
| --- | --- |
| A test tenant with a Windows 365 licence and a working Cloud PC | Nothing can be tested without a real target |
| An administrator who can approve the app's permissions | Microsoft requires admin approval for the permissions we need; without it every result is ambiguous |
| A Linux machine set up with the tools | The test must run **on Linux**, deliberately — testing elsewhere would hide the exact failure we are looking for |
| **An answer on the tenant's security policy** | See below — this is the one that can invalidate everything |
| A written legal position on two questions | Whether we may reuse Microsoft's own application identity, and whether we may inspect the network traffic of Microsoft's client to learn the format |

### The prerequisite that can invalidate the whole test

Many organizations enforce a security policy requiring that connections come from a **company-managed device**. A
Linux desktop is not a managed device in that sense, so in such an organization sign-in fails — regardless of whether
our approach is sound.

If our test tenant enforces that policy, **a failure on day 2 tells us nothing**: we would not know whether the
approach is impossible or the tenant simply blocked us. So we establish the tenant's policy first. If it enforces
device compliance, the sprint waits until we have a tenant that does not.

### The irreversible choice on day 1

Registering our application with Microsoft requires choosing which organization it belongs to, and **that choice
cannot be changed afterwards**. The resulting identifier is embedded in every copy of the software we distribute.
Moving it later is not a migration — it is a new identity and a forced update for every installed client. Half an hour
of thought on day 1 avoids that entirely.

---

## 7. What five days does not buy

Worth being blunt, because "five-day sprint" is easily misread as "five days to a product."

- **Not a working desktop session.** Even in the best outcome, days 4–5 reach the first step of the remaining work,
  not a usable connection.
- **Not the full client.** Multiple accounts, session management, the management actions, packaging and installation —
  all outside this sprint.
- **Not a schedule for the rest.** Producing one is the *output* of the sprint, not an input to it. Every estimate
  beyond day 5 depends on the day-3 answer.

What five days buys is a decision made on evidence instead of on hope, at a cost the project can absorb if the answer
is no.

---

## 8. Two risks the team should know from day one

Both are recorded deliberately in the specification, because both are easy to soften by accident.

**The addressable market may be smaller than it looks.** Organizations that enforce the managed-device policy
described above cannot use this client until we add support for integrating with the operating system's identity
broker — work that is committed but scheduled after the first release. And those organizations are
*disproportionately the ones running Windows 365 in the first place*. "It works in our test tenant" must never be
reported as "it works for customers."

**The critical path runs through something undocumented.** The endpoint we depend on has no contract and no support
commitment. Microsoft can change it, and one capability the web client used to offer has already been withdrawn. Even
in the best case, this is a client that needs good diagnostics in the field and a team that expects to react.

---

## 9. How to read the repository

There is no application code yet. The repository is currently the thinking, written down.

| Document | What it is |
| --- | --- |
| [`README.md`](../README.md) | Short public summary of the product and its known limitations |
| [`spec.md`](../spec.md) | The full specification. **Section 14 is a decision register** — twenty decisions, each with its reasoning and the condition under which we would revisit it. Start there |
| [`gapsandrecommendations.md`](../gapsandrecommendations.md) | An honest assessment of what the specification still gets wrong or leaves open — 54 findings |
| [`projectmanagement/projectplan.md`](../projectmanagement/projectplan.md) | The five-day sprint plan in operational detail |

Day-to-day execution is tracked in **Linear**, split into three projects: the prerequisites to
procure, the open specification questions, and the sprint itself.

**One convention worth adopting immediately.** When this project reports status, it separates three things that are
easy to blur: what is **verified** (we tested it), what is **decided but unverified** (we chose it, and it could still
turn out wrong), and what is **assumed** (we have not checked). Most of the value in these documents is in keeping
those three apart. Right now, almost everything is in the second and third categories — which is precisely what the
five-day sprint begins to change.
