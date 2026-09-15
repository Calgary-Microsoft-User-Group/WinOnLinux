# Change: add-ui-shell

## Why

spec.md §4 specifies the entire user-facing surface — main window, account switcher, grouped resource list,
connect controls, action menus, and the six UI states — but no UI exists. This change implements that surface
as the composition layer over the auth manager, resource providers, launchers, and action service, turning
their states and results into the behavior the FR acceptance criteria describe.

## What Changes

- Main window per §4.1: account switcher (top-left) with per-account auth-state badges, refresh control, and a
  resource list grouped by provider ("Windows 365", "Azure Virtual Desktop") with AVD entries further grouped
  by workspace.
- Per-resource split button per §4.2: primary action is the resource's remembered last-used method, dropdown
  offers Native and Web; unavailable methods are disabled — never hidden — with the specific reason in the
  tooltip (FR-2-AC-1). Last-used method persists per resource across restart via the app-foundation state
  store (FR-2-AC-2).
- Cloud PC actions menu per §4.3: Restart, Rename, Troubleshoot, Reset (Reprovision) with the destructive
  confirmation dialog; Restore/Resize rendered only for admin-capable accounts (FR-5-AC-3 rendering).
- UI states per §4.4: Loading skeleton, Empty vs distinct no-licence Empty (FR-1-AC-3), Offline (cached list
  greyed, connects disabled), Error with retry that never clears the previous list (FR-1-AC-4), per-account
  non-blocking ReauthRequired banner (FR-3-AC-5), and the guided Consent-required screen surface.
- State-gated enablement rendering per §7.4: unknown or unrecognized status renders as not connectable;
  actions whose precondition fails are disabled with a reason; conflicting actions suppressed after an invoked
  transition until the next refresh.
- Launch feedback per §4.2: spinner until the native session window appears or the browser is spawned; a
  native launch failure presents the web fallback in the same failure surface, one action (FR-2-AC-5).
- Error presentation per §9's messaging rules: messages state what happened and what the user or admin can do;
  raw error codes go to logs only.

## Capabilities

### New Capabilities

- `ui-shell`: the application's visible behavior — window structure, account switching surface, resource
  presentation, connect/action controls, UI states, and error presentation rules.

### Modified Capabilities

<!-- none -->

## Impact

- Depends on `app-foundation` (state store, task groups, logging), `auth-account-manager` (account list,
  auth states, active account), and `cloudpc-enumeration` (resource data). Consumes the web/native launchers
  and the actions service through their interfaces; their behavior is specified in their own changes.
- Owns the per-resource connection-method preference store (keyed by account and resource).
- Pure-logic pieces (grouping, enablement mapping, preference persistence) are unit-testable; the widget layer
  itself is verified manually per §11.2 (M).
