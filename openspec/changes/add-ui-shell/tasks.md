# Tasks: add-ui-shell

## 1. View-model layer (widget-free, CI-testable)

- [x] 1.1 Define view-model types: account entry (badge, active flag), resource entry (name, type, status,
      grouping key), method availability (enabled, reason), action availability
- [x] 1.2 Implement provider/workspace grouping from enumeration results (§4.1)
- [x] 1.3 Implement the state-selection function: Loading / Empty / no-licence Empty / Offline / Error /
      normal, with error-keeps-list semantics (FR-1-AC-3, FR-1-AC-4, §4.4)
- [x] 1.4 Implement §7.4 enablement mapping as mechanism + data: unknown status → not connectable; disabled
      cells carry reason strings; post-action conflict suppression until next refresh
- [x] 1.5 Implement per-resource method preference store on the app-foundation state store, keyed by home
      account ID and resource key (FR-2-AC-2, D-13)
- [x] 1.6 Unit tests for 1.2–1.5, including unknown-status fail-closed and both empty states

## 2. Window and account switcher

- [x] 2.1 Build the main window: header bar, account switcher menu with badges, Add account…, per-account
      sign-out, Refresh (§4.1)
- [x] 2.2 Wire account switch to the auth manager and task-group cancellation; verify the switcher reflects
      active-account swap within the NFR-2 budget (FR-3-AC-2 UI half)
- [x] 2.3 Implement per-account ReauthRequired banner with inline re-auth button (FR-3-AC-5, §4.4)

## 3. Resource list and connect controls

- [x] 3.1 Build grouped resource list bound to the view-models, with status chips
- [x] 3.2 Build the split button: remembered primary method, both methods always present, disabled entries
      with visible reasons (FR-2-AC-1/AC-2)
- [x] 3.3 Implement launch feedback spinner and the shared failure surface with one-action web fallback
      (§4.2, FR-2-AC-5) — failure surface + one-action web fallback implemented
      (`ui_shell._present_launch_failure`); the spinner-until-session-window half lands with the launcher
      changes (no launch exists in this build to give feedback on; the dispatch hook `_launch` is in place)

## 4. Cloud PC actions UI

- [x] 4.1 Build the actions menu (Restart, Rename, Troubleshoot, Reset) bound to action availability
      view-models (§4.3)
- [x] 4.2 Implement the reprovision destructive-confirmation dialog; cancel dispatches nothing (FR-5-AC-2)
- [x] 4.3 Render Restore/Resize only on admin capability; capability-unknown renders not-capable (FR-5-AC-3)
- [x] 4.4 Implement progress + success/failure toasts and status-chip update on next refresh (FR-5-AC-1 UI)
      — request toast, §7.4 pending suppression, and chip re-render on refresh implemented; real
      success/failure outcomes arrive with add-cloudpc-actions' dispatch through `_dispatch_action`

## 5. Remaining states and error presentation

- [x] 5.1 Implement Offline rendering (greyed cached list, connects disabled) and Consent-required surface
      hosting the guided flow from the auth change (§4.4, §9)
- [x] 5.2 Implement the §9 message rules: plain-language what/next-step text, raw codes to redacted logs only;
      security-error presentation for validation rejections (FR-2-AC-6) — all user-facing text flows from
      backend `user_message` fields; the FR-2-AC-6 security-error surface has no producer until
      add-connection-config-provider lands (its rejections arrive through the launch failure surface)
- [ ] 5.3 Manual test pass across the §4.4 state matrix and both accounts signed in, recorded per §11.2 (M)
      — requires a real desktop session and signed-in tenant; not performable in the headless container this
      change was implemented in. Includes the 2.2 NFR-2 switcher-latency observation.
