## ADDED Requirements

### Requirement: Windows 365 direct-launch URL composition
The web launcher SHALL compose Windows 365 launch URLs as
`https://windows.cloud.microsoft/webclient/ent/<CloudPc.Id>` using exactly the Graph `id` retained at
enumeration (FR-1-AC-2), with `?tenant=<tenantID>` placed before the fragment and `#loginHint=<active account
UPN>` as the final URL component (§5.3, FR-2-AC-3). The `?tenant=` component SHALL be included when the
account's home tenant differs from the browser's default; `#loginHint=` SHALL always be appended.

#### Scenario: URL for a Cloud PC with tenant and login hint
- **WHEN** a web launch is requested for a Cloud PC with Graph id `X` under active account `user@contoso.com`
  in tenant `T`
- **THEN** the composed URL is `https://windows.cloud.microsoft/webclient/ent/X?tenant=T#loginHint=user@contoso.com`,
  with the query preceding the fragment and `#loginHint=` as the last component

#### Scenario: No second lookup for the resource identifier
- **WHEN** the launch URL is composed
- **THEN** the `CloudPc.Id` used is the identifier already held from enumeration, and no additional Graph call
  is made to resolve it

### Requirement: Microsoft-issued launch URL preference
The web launcher SHALL prefer the `cloudPcLaunchUrl` returned by Graph beta
`GET /me/cloudPCs/{id}/retrieveCloudPcLaunchDetail` when the beta feature flag (§7.2) is enabled and the call
succeeds, and SHALL fall back to the constructed `ent/` URL on any failure without user-visible error. The
deprecated `getCloudPcLaunchInfo` SHALL never be called (§12 risk 7).

#### Scenario: Issued URL preferred when available
- **WHEN** `retrieveCloudPcLaunchDetail` returns a `cloudPcLaunchUrl` for the Cloud PC
- **THEN** the browser is opened at that URL rather than the constructed `ent/` URL

#### Scenario: Silent fallback on beta failure
- **WHEN** the launch-detail call fails, returns an unexpected shape, or the beta flag is off
- **THEN** the constructed `ent/` URL is used, the launch proceeds normally, and the beta failure is logged
  for triage rather than surfaced

### Requirement: AVD direct-launch URL composition
The web launcher SHALL compose AVD launch URLs as
`https://windows.cloud.microsoft/webclient/avd/<workspaceID>/<resourceID>` with the same `?tenant=` and
`#loginHint=` rules, using admin-provisioned workspace/resource IDs in Phase 0 (§11.1). An AVD entry without
admin-provided IDs SHALL show the web method disabled — never hidden — with the specific reason (FR-2-AC-1).

#### Scenario: AVD resource with provisioned IDs
- **WHEN** a web launch is requested for an AVD desktop with workspace ID `W` and resource ID `R`
- **THEN** the composed URL is `https://windows.cloud.microsoft/webclient/avd/W/R` with tenant and loginHint
  components appended per the composition rules

#### Scenario: AVD resource without IDs
- **WHEN** an AVD entry has no admin-provisioned workspace/resource IDs
- **THEN** the web method is disabled with the reason "Web launch unavailable: workspace/resource ID unknown"
  and no URL is composed

### Requirement: System browser handoff
The web launcher SHALL open composed URLs in the system browser — via the OpenURI portal under Flatpak (§5.8) —
and SHALL NOT embed a browser. A silent token acquisition for the owning account SHALL precede any Graph call
made during launch (FR-4-AC-1).

#### Scenario: Browser lands on the active account
- **WHEN** the browser opens a launch URL carrying `#loginHint=<active UPN>`
- **THEN** the web client lands on the active account without presenting an account picker (FR-2-AC-3)

#### Scenario: Browser fails to spawn
- **WHEN** the system browser cannot be spawned
- **THEN** an error toast is shown offering the composed URL for manual copy (§9), and the failure is not
  reported as a resource or account error

### Requirement: UPN confined to the URL fragment
The web launcher SHALL place the UPN only in the `#loginHint` fragment — never in the query string or path —
so it is not transmitted in the HTTP request (§10.5). The trade-off that the fragment enters browser history
SHALL be stated in user documentation.

#### Scenario: UPN never in the query
- **WHEN** any launch URL is composed
- **THEN** the UPN appears only after `#loginHint=` and in no other URL component

### Requirement: Second-RemoteApp warning
The web launcher SHALL warn before opening a second RemoteApp web tab from the same host pool within one app
session, because the second launch disconnects the first (§12 risk 9). The warning SHALL allow the user to
proceed or cancel.

#### Scenario: Second RemoteApp from one host pool
- **WHEN** a RemoteApp web launch is requested and another RemoteApp from the same host pool was already
  launched this session
- **THEN** a warning states the first session will be disconnected, and the launch proceeds only on
  confirmation
