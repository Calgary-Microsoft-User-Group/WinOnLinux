# ui-shell Specification

## ADDED Requirements

### Requirement: Main window structure

The main window SHALL present an account switcher listing every signed-in account with the active one marked
and a per-account auth-state badge, plus "Add account…" and per-account sign-out entries, and a resource list
grouped by provider with AVD entries further grouped by workspace (§4.1). Each resource entry SHALL show name,
type, and live status where available (FR-1-AC-2).

#### Scenario: Accounts and resources render grouped

- **WHEN** two accounts are signed in and the active account has Cloud PCs and AVD resources in two workspaces
- **THEN** the switcher lists both accounts with the active one marked, and the list shows a Windows 365 group
  and an Azure Virtual Desktop group with resources under their workspace headings

#### Scenario: ReauthRequired badge in the switcher

- **WHEN** a signed-in, non-active account enters the ReauthRequired state
- **THEN** its switcher entry shows the reauthentication-required badge while the active account's resources
  remain fully interactive (FR-3-AC-5)

### Requirement: Per-resource connect controls

Every resource entry SHALL expose both connection methods. An unavailable method SHALL be shown disabled —
never hidden — with the specific reason visible to the user (FR-2-AC-1). The primary action of the split
button SHALL be the resource's last-used method, persisted per resource per account across application
restart (FR-2-AC-2).

#### Scenario: Unavailable method disabled with reason

- **WHEN** a resource's native method is unavailable because no connection configuration can be acquired
- **THEN** the Native entry renders disabled with the stated reason, and the Web entry remains selectable

#### Scenario: Last-used method survives restart

- **WHEN** the user connects to a resource via Web, quits the application, and relaunches it
- **THEN** that resource's split-button primary action is Connect (Web browser)

### Requirement: State-gated availability rendering

The shell SHALL gate connect methods and actions on the resource's current state per §7.4: a status not known
to permit connection offers no enabled connect method; unknown or unrecognized status values are treated as
not connectable; an action whose precondition fails is disabled with a reason; after an invoked action's state
transition, conflicting actions are suppressed until the next refresh resolves the new state.

#### Scenario: Unknown status fails closed

- **WHEN** a Cloud PC reports a status value the application does not recognize
- **THEN** both connect methods render disabled with a reason, not optimistically enabled

#### Scenario: Conflicting actions suppressed after an invocation

- **WHEN** the user invokes Restart on a Cloud PC and the refresh has not yet resolved the new state
- **THEN** actions conflicting with the restart transition are disabled until the next refresh completes

### Requirement: Cloud PC action menu rendering

Each Cloud PC entry SHALL offer Restart, Rename, Troubleshoot, and Reset (Reprovision) (§4.3). Reprovision
SHALL require an explicit typed or checked confirmation explaining that the Cloud PC is wiped and rebuilt, and
cancelling SHALL dispatch nothing (FR-5-AC-2 UI half). Restore and Resize SHALL be rendered only when the
active account is determined admin-capable; capability-unknown renders as not capable (FR-5-AC-3, D-7).

#### Scenario: Reprovision confirmation gate

- **WHEN** the user selects Reset (Reprovision) and dismisses the confirmation dialog without confirming
- **THEN** no action call is dispatched and the entry's state is unchanged

#### Scenario: Admin-only actions hidden for plain accounts

- **WHEN** the active account has no admin capability signal
- **THEN** Restore and Resize do not appear in the action menu

### Requirement: UI states

The shell SHALL implement the §4.4 states: Loading skeleton during enumeration; an Empty state for no
resources and a **distinct** no-licence empty state, neither rendered as an error (FR-1-AC-3); Offline with
the cached list greyed and connects disabled; Error shown inline with retry while the previously enumerated
list stays visible (FR-1-AC-4); a per-account non-blocking ReauthRequired banner whose inline button starts
interactive re-auth for that account only; and a guided consent-required surface.

#### Scenario: No-licence state is distinct and not an error

- **WHEN** enumeration for the active account returns the no-licence condition
- **THEN** the no-licence empty state renders, visibly distinct from the plain empty state, with no error
  styling

#### Scenario: Failed refresh keeps the list

- **WHEN** a refresh fails after a previous successful enumeration
- **THEN** the previously enumerated list remains visible with an error indicator and a retry affordance, and
  the list is not cleared

#### Scenario: Reauth banner scoped to one account

- **WHEN** the active account enters ReauthRequired while a second account is signed in
- **THEN** the banner names the affected account with an inline re-auth button, and switching to the second
  account shows its resources fully functional without the banner blocking anything

### Requirement: Launch feedback and fallback surface

The shell SHALL show launch progress on the resource entry until the native session window appears or the
browser is spawned (§4.2). A native launch failure SHALL present the web fallback in the same failure surface
so the user can fall back in one action (FR-2-AC-5).

#### Scenario: Native failure offers web fallback in one action

- **WHEN** a native launch fails after the user clicks Connect (Native)
- **THEN** the failure surface for that resource presents the reason and a single-action web-launch fallback

### Requirement: Error message presentation

User-visible error messages SHALL state what happened and what the user or their admin can do; raw error codes
SHALL go to logs only (§9). A configuration rejected by field validation SHALL be presented as a security
error, distinct from connection errors (FR-2-AC-6 presentation).

#### Scenario: Raw codes stay out of dialogs

- **WHEN** a Graph call fails with an HTTP status and error body
- **THEN** the dialog or banner text describes the condition and remedy in plain language and the raw
  code/body appears only in the (redacted) log

#### Scenario: Security rejection presented as security error

- **WHEN** the connection-config provider rejects a configuration on validation grounds
- **THEN** the shell presents a security-error message for that launch, not a generic connection failure, and
  no retry re-offers the same configuration
