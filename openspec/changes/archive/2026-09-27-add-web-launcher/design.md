## Context

§5.3 specifies two direct-launch URL families (`ent/` for Windows 365, `avd/` for AVD) plus a Microsoft-issued
per-resource `cloudPcLaunchUrl` from Graph beta `retrieveCloudPcLaunchDetail`. The direct-launch URLs are a
documented end-user feature, **not a versioned API contract** (§12 risk 7) — treat the format as
decided-but-unverified against churn, and keep composition in one module so a format change is a one-file fix.
Phase 0 ships web-only, so this component is the product's only working connect path until Stage 3.

What is verified vs assumed, per the repo's honesty conventions:

- *Verified (documented)*: the `ent/`/`avd/` URL shapes, `?tenant=`/`#loginHint=` composition rules, and
  `retrieveCloudPcLaunchDetail` returning `cloudPcLaunchUrl` (§5.3, §7.1 — July 2026 documentation).
- *Decided but unverified*: that the browser lands on the active account without a picker (FR-2-AC-3 is verified
  by M-level tests at Phase 0, §11.2).
- *Assumed*: whether tenant/loginHint components may be appended to the Microsoft-issued `cloudPcLaunchUrl`
  (§5.3 is silent) — see Open Questions.

## Goals / Non-Goals

**Goals:**

- One URL-composition module, unit-testable with no network (§13.2, §13.4).
- FR-2-AC-3 exactly: query before fragment, `#loginHint=` last, active-account UPN always appended to
  constructed URLs.
- Prefer the Microsoft-issued launch URL when the beta call yields one; degrade silently to construction.
- Browser handoff that works identically under Flatpak (OpenURI portal) and bare desktop.

**Non-Goals:**

- Native launch (own change, `add-native-launcher`), per-resource method persistence and the split-button UI
  (`add-ui-shell`, FR-2-AC-2), AVD feed-based ID discovery (`add-avd-feed-provider` — Phase 0 uses
  admin-provisioned IDs only, §11.1).
- Embedding a browser. The system browser is the §5.3 contract; under Flatpak the portal picks it (§10.5).

## Decisions

- **URL builder is a pure function** `buildWebLaunchUrl(resource, account) -> URL` with no I/O, so §11.2's
  U-level verification for FR-2-AC-1…AC-3 is fixture-only. Alternative — composing inline in the UI layer —
  rejected because FR-2-AC-3's ordering rules are exactly the kind of detail that regresses without a focused
  unit surface.
- **Launch-detail call is optional-fast-path**: issued URL preferred when the beta flag is on and the call
  succeeds within the enumeration refresh; any error → constructed URL, no user-visible failure. Rationale:
  §7.2 marks beta "production use not supported"; the constructed URL is documented and sufficient, so the
  beta call must never sit on the critical path. `getCloudPcLaunchInfo` is never referenced (§7.1).
- **Fragment handling for issued URLs**: append `#loginHint=<UPN>` to the issued URL only when it carries no
  fragment; never modify its path or query. Conservative reading of §5.3 pending the Open Question below.
- **Silent acquisition precedes the launch-detail call** (FR-4-AC-1); the launch itself (opening a URL) needs
  no token.
- **RemoteApp warning is launcher-side state**: the launcher remembers, per host pool and app session, that a
  RemoteApp web tab was opened, and warns before opening a second (§12 risk 9). No cross-session persistence —
  a stale warning is worse than none.

## Risks / Trade-offs

- [Direct-launch URL format churns — it is a feature, not an API] → single composition module; the M-level
  Phase 0 test in §11.2 catches breakage per release; issued-URL preference reduces exposure where the beta
  call works.
- [Beta `retrieveCloudPcLaunchDetail` contract changes] → §7.2 posture: behind the beta flag, constructed URL
  as degradation, log for triage. Web launch never breaks with the flag off.
- [UPN enters browser history via the fragment] → accepted §10.5 trade-off, restated in user documentation;
  UPN is never placed in the query string.
- [Portal chooses an unexpected browser under Flatpak] → accepted §5.8 cost; the copyable-URL toast (§9) is
  the escape hatch when spawn fails outright.

## Open Questions

- May tenant/loginHint components be appended to the Microsoft-issued `cloudPcLaunchUrl`, or must it be opened
  verbatim? §5.3 is silent. Interim rule above is conservative; confirm against a live tenant during Phase 0
  integration testing and amend §5.3 with the finding.
