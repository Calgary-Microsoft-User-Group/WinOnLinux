## ADDED Requirements

### Requirement: Self-service action invocation
The Management Action service SHALL invoke Restart (`reboot`), Rename, and Troubleshoot via
`POST /me/cloudPCs/{id}/...` (Graph beta, §7.1), each preceded by a silent token acquisition for the owning
account (FR-4-AC-1), each showing progress and surfacing the outcome, with the entry's status chip reflecting
the transition on the next refresh (FR-5-AC-1).

#### Scenario: Restart on a permitting state
- **WHEN** Restart is invoked on a Cloud PC whose current status permits it
- **THEN** the documented beta `reboot` call is issued, progress is shown, the outcome is surfaced, and the
  status chip reflects the transition on the next §7.3 refresh

#### Scenario: Acquisition precedes the call
- **WHEN** any action is invoked
- **THEN** a silent token acquisition for the owning account completes before the Graph call is issued

### Requirement: Beta feature flag isolation
All `/me` action endpoints SHALL sit behind a beta-API feature flag that isolates Graph version selection in
one place (§7.2). With the flag off, actions SHALL be disabled with a reason rather than hidden. The service
SHALL be switchable to v1.0 paths without touching callers when Microsoft promotes them.

#### Scenario: Flag off disables actions
- **WHEN** the beta feature flag is off
- **THEN** every self-service action is disabled with a reason, and no beta endpoint is called

### Requirement: Reprovision confirmation gate
Reprovision SHALL require explicit typed or checked confirmation stating that the Cloud PC is wiped and
rebuilt (§4.3, §10.6). Cancelling SHALL issue no call. The reprovision call SHALL never be batched and never
automatically retried (FR-5-AC-2).

#### Scenario: Cancel issues no call
- **WHEN** the user opens the reprovision confirmation and cancels
- **THEN** no Graph call is issued and no state changes

#### Scenario: No automatic retry of reprovision
- **WHEN** a reprovision call fails transiently
- **THEN** the failure is surfaced and the call is not retried automatically

### Requirement: Admin capability gating
Restore and Resize SHALL be hidden for accounts not determined admin-capable from token role/`wids` claims
(D-7); capability-unknown SHALL be treated as not-capable (FR-5-AC-3). The UI SHALL never be offered an
action that would then fail on authorization.

#### Scenario: Plain account sees no admin actions
- **WHEN** the active account's token carries no admin role/`wids` claims
- **THEN** Restore and Resize are hidden for every Cloud PC of that account

#### Scenario: Capability unknown treated as not capable
- **WHEN** capability cannot be determined from the token claims
- **THEN** Restore and Resize are hidden

### Requirement: State-gated enablement
An action whose precondition the Cloud PC's current status does not satisfy SHALL be disabled with a reason
rather than offered and failed (§7.4). Unknown or unrecognized status values SHALL disable, never
optimistically enable. A state transition triggered by an action SHALL suppress conflicting actions until the
next refresh resolves the new state.

#### Scenario: Unknown status disables
- **WHEN** a Cloud PC reports a status value the client does not recognize
- **THEN** all actions on that entry are disabled, each with a reason

#### Scenario: Transition suppresses conflicting actions
- **WHEN** Restart has been accepted and the Cloud PC's new state is not yet resolved
- **THEN** conflicting actions are suppressed until the next refresh resolves the state

### Requirement: Completion observed by polling
Action completion SHALL be observed via the existing §7.3 status refresh — an immediate refresh after an
accepted action, then the normal poll — bounded by a stated timeout, with no operation-polling machinery
(D-8).

#### Scenario: Outcome surfaces through refresh
- **WHEN** an action is accepted by Graph
- **THEN** an immediate status refresh is requested, and the outcome is reflected from refresh data rather
  than from a dedicated operation-status API

### Requirement: Consent, contract, and national-cloud failure handling
The service SHALL map failures per §9: a `403` for missing consent SHALL surface the guided admin-consent
flow, not a generic error (FR-5-AC-4); a beta response with an unexpected shape SHALL disable only the
affected action with "Action unavailable — Microsoft API change" and log for triage, leaving enumeration and
both connect methods working (FR-5-AC-5); in a national cloud without Cloud PC Graph APIs, FR-5 SHALL be
disabled wholesale with an explanatory message (FR-5-AC-6); `429` SHALL honor `Retry-After` with backoff.

#### Scenario: Unconsented tenant
- **WHEN** an action returns `403` insufficient privileges
- **THEN** the guided admin-consent flow is presented, explaining that a tenant admin must approve and
  offering the admin-consent URL for forwarding

#### Scenario: Mutated beta shape
- **WHEN** an action response fails shape validation
- **THEN** only that action is disabled with "Action unavailable — Microsoft API change", the redacted
  response is logged, and enumeration and connect methods keep working

#### Scenario: National cloud without Cloud PC APIs
- **WHEN** the account's cloud environment is one where Cloud PC Graph APIs are unavailable
- **THEN** the entire FR-5 action surface is disabled with an explanatory message rather than failing per
  action
