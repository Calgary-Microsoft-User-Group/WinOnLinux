# Design: add-ui-shell

## Context

§4 is written toolkit-agnostic; D-16 fixes the toolkit as GTK4/libadwaita. The shell sits on top of every other
component and owns no protocol logic: its job is faithful rendering of states the other layers already model
(auth states from §6.4, resource states from §7.4, launch outcomes from §9) and correct dispatch of user intent
into those layers. The riskiest part is not widgets but the state mapping — §7.4's "unknown means not
connectable" and §4.4's six states each have acceptance criteria attached.

## Goals / Non-Goals

**Goals:**

- Implement §4.1–§4.4 on GTK4/libadwaita.
- Encode the §7.4 enablement rules and §9 messaging rules in testable, widget-free logic.
- Persist per-resource method preference (FR-2-AC-2) via the app-foundation state store.

**Non-Goals:**

- No auth, Graph, feed, or launch logic — the shell calls interfaces defined by those changes.
- No Cloud PC status × action enablement matrix content: §7.4 marks the full matrix as outstanding (P1,
  G-19); the shell implements the mapping mechanism and the stated defaults (unknown → not connectable) so the
  matrix drops in as data when it lands.
- No native-session window management — FreeRDP owns its window (D-1).

## Decisions

- **View-model layer between services and widgets.** Grouping, badge selection, enablement, tooltip-reason
  text, and state selection (loading/empty/no-licence/offline/error) are computed in plain Python view-model
  objects; widgets bind to them. Rationale: §11.2 verifies most UI criteria manually, but the mappings behind
  them (FR-1-AC-3's two empty states, §7.4 gating) are unit-testable only if kept out of widget code.
  Alternative — logic in signal handlers — rejected as untestable in CI (§13.4).
- **Disabled-never-hidden as a rendering invariant.** The split-button model always contains both methods;
  availability is a (enabled, reason) pair (FR-2-AC-1). Hiding is not representable, so the invariant cannot
  regress silently.
- **Preference store schema.** `{account home ID → {resource key → method}}` in one state-store file with its
  own `schemaVersion` (D-13). Resource key is the Graph `id` for Cloud PCs (FR-1-AC-2) and workspace+resource
  ID for AVD.
- **Per-account banners are list-adjacent, not modal.** ReauthRequired renders as a banner scoped to the
  account's rows plus a badge in the switcher (FR-3-AC-5); nothing blocks other accounts' interaction (§6.4).
- **Error persistence rule.** A failed refresh sets an error indicator on the existing list rather than
  replacing the list widget's model (FR-1-AC-4); the model swap happens only on successful enumeration.

## Risks / Trade-offs

- [libadwaita lacks a stock split button with per-item disable+tooltip semantics] → compose from
  `Adw.SplitButton`/menu models; if tooltips on disabled menu items prove unreliable under GTK4, surface the
  reason as a subtitle row instead — the FR-2-AC-1 requirement is the stated reason being visible, not the
  tooltip mechanism specifically.
- [State explosion across 6 UI states × per-account overlays] → single state-selection function in the
  view-model with exhaustive unit tests; widgets render whatever it selects.
- [The §7.4 matrix is outstanding (G-19)] → mechanism/data split above; defaults fail closed (unknown → not
  connectable), so the missing matrix degrades availability, never safety.

## Open Questions

- Whether the consent-required surface is a dialog or an in-window page — resolved during implementation
  against the guided-consent flow defined by the auth change (§9); the requirement is only that it is guided,
  not its container.
