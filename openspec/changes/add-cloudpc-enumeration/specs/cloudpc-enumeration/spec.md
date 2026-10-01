# cloudpc-enumeration — Windows 365 Cloud PC enumeration and status refresh

Implements the Windows 365 half of FR-1 via Microsoft Graph v1.0 (spec.md §3 FR-1, §7.1, §7.3), plus the Phase 0
admin-provisioned AVD bookmark list (FR-1-AC-5 partial, §11). AVD feed enumeration is specified separately in the
`avd-feed` capability.

## ADDED Requirements

### Requirement: Cloud PCs are enumerated completely via Graph v1.0 with paging

For the active account, the application SHALL enumerate Cloud PCs via Graph v1.0 `GET /me/cloudPCs` (delegated
`CloudPC.Read.All`), following every page of a paged response before surfacing the result, within the NFR-2 budget
(FR-1-AC-1). Every call SHALL be immediately preceded by silent token acquisition for the owning account
(FR-4-AC-1).

#### Scenario: Paged response

- **WHEN** `GET /me/cloudPCs` returns results across multiple pages via `@odata.nextLink`
- **THEN** the surfaced list contains every Cloud PC from every page, and no partial page is ever presented as a
  complete result

#### Scenario: Enumeration scoped to the active account

- **WHEN** two accounts are signed in and enumeration runs
- **THEN** only the active account's Cloud PCs are requested and displayed

### Requirement: The Graph id is retained as the sole downstream identifier

Each entry SHALL display name and status and SHALL retain the Graph `id` as the identifier used for web launch and
all FR-5 actions, with no second lookup (FR-1-AC-2).

#### Scenario: Identifier reuse without re-query

- **WHEN** a user invokes web launch or a management action on an enumerated Cloud PC
- **THEN** the call uses the `id` captured at enumeration time and issues no additional lookup call first

### Requirement: Empty and no-licence states are distinct and are not errors

An account with a Cloud PC licence but nothing provisioned SHALL show the empty state; an account with no licence
(Graph `404` on `/me/cloudPCs`) SHALL show the distinct no-licence empty state. Neither SHALL render as an error
(FR-1-AC-3, §9).

#### Scenario: Licensed but unprovisioned

- **WHEN** enumeration returns an empty collection
- **THEN** the empty state is shown ("no resources assigned"), with no error styling

#### Scenario: No licence

- **WHEN** enumeration returns HTTP `404`
- **THEN** the no-licence empty state is shown ("No Cloud PC is assigned to this account"), distinct from the
  ordinary empty state, with no error styling

### Requirement: A failed refresh never clears the list

A refresh that fails SHALL leave the previously enumerated list visible with an error indicator; it SHALL never
clear the list (FR-1-AC-4).

#### Scenario: Refresh failure with existing data

- **WHEN** a periodic or manual refresh fails after a prior successful enumeration
- **THEN** the previous list remains visible, an error indicator with retry appears, and no entry is removed

### Requirement: Status refresh follows the §7.3 lifecycle

The application SHALL refresh resource status on sign-in, on account switch, on manual refresh, periodically while
foregrounded (60 s interval, paused in background), and immediately after any invoked management action (§7.3,
D-8). Account switch SHALL cancel the previous account's in-flight enumeration (FR-3-AC-2).

#### Scenario: Background pause

- **WHEN** the main window loses foreground status
- **THEN** periodic polling pauses, and resumes on return to foreground

#### Scenario: Switch cancels enumeration

- **WHEN** the active account changes while its enumeration is in flight
- **THEN** the previous account's requests are cancelled and the new account's enumeration begins

### Requirement: Throttling is honored without user-visible errors

Graph `429` responses SHALL be honored via `Retry-After` and exponential backoff, with no user-visible error unless
throttling persists (§9).

#### Scenario: Throttled refresh

- **WHEN** a refresh receives `429` with `Retry-After: 30`
- **THEN** the retry waits at least 30 seconds, the existing list stays visible, and no error surfaces on the first
  throttle

### Requirement: Graph traffic honors the system proxy configuration

All enumeration traffic SHALL honor the environment's proxy configuration (`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`
and desktop proxy settings), and proxy failures SHALL be reported distinguishably from plain network failures
(§5.9, §9).

#### Scenario: Proxied environment

- **WHEN** the environment defines an HTTPS proxy
- **THEN** Graph requests route through it, and a proxy rejection is reported naming the proxy as the cause rather
  than as "offline"

### Requirement: Consent-required is distinguished from a generic failure

Graph `403` on `/me/cloudPCs` (tenant admin consent missing) SHALL be surfaced as a distinct result, separate from
both the ordinary transport/5xx `Failed` case and the no-licence `404` case, so the UI can offer the guided
admin-consent flow rather than a generic "refresh failed" error (closes G-42).

#### Scenario: Consent required

- **WHEN** enumeration returns HTTP `403`
- **THEN** a distinct consent-required result is surfaced, not treated as an ordinary `Failed` error and not
  conflated with the no-licence `404` state

### Requirement: Phase 0 AVD entries come from admin-provisioned bookmarks

Until feed enumeration lands, AVD entries SHALL appear only for admin-provisioned workspace/resource IDs
(FR-1-AC-5 Phase 0), stored per D-13 (JSON under `XDG_STATE_HOME` with `schemaVersion`), rendered grouped by
workspace with type shown (desktop / RemoteApp). Such entries SHALL offer web launch only; the native method is
shown disabled with its reason (FR-2-AC-1, §11 Phase 0).

#### Scenario: Bookmarked AVD desktop

- **WHEN** an admin-provisioned bookmark with workspace and resource IDs is present
- **THEN** the AVD section lists it under its workspace with web launch available and native disabled with a
  stated reason

#### Scenario: No bookmarks

- **WHEN** no AVD bookmarks are configured
- **THEN** no AVD section error is shown; the Cloud PC section is unaffected
