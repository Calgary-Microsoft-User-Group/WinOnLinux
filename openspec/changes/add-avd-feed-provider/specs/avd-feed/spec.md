# avd-feed — delta specification

## ADDED Requirements

### Requirement: Feed discovery resolves the workspace feed

The provider SHALL perform feed discovery by calling `https://rdweb.wvd.microsoft.com/api/arm/feeddiscovery` with a
bearer token for the active account, and SHALL derive the workspace-feed download URL(s) from the discovery
response as documented by the Stage 1 schema capture (§8). The provider SHALL NOT hard-code any second-leg URL.

#### Scenario: Successful discovery chains to workspace feed download

- **WHEN** feed discovery returns a successful response for the active account
- **THEN** the provider downloads each workspace feed from the URL(s) the discovery response supplies, and no other
  endpoint is contacted

#### Scenario: Discovery failure is isolated to the AVD section

- **WHEN** the feed-discovery call fails (network error, 5xx, or unparseable response)
- **THEN** the AVD section of the resource list shows an inline error with a retry action, and Cloud PC enumeration
  and both Cloud PC connect methods remain fully functional (§9)

### Requirement: AVD resources are enumerated from the workspace feed

The provider SHALL enumerate the AVD workspaces, desktops and RemoteApps assigned to the active account from the
workspace feed, grouped by workspace, with no admin pre-provisioning (FR-1-AC-5). Each entry SHALL carry the
identifiers required for web launch (§5.3) and for connection-config acquisition (§5.2).

#### Scenario: Desktops and RemoteApps appear grouped by workspace

- **WHEN** an account is assigned one workspace containing a published desktop and a published RemoteApp, and the
  user signs in
- **THEN** the resource list shows that workspace as a group containing both entries, typed desktop and RemoteApp
  respectively, without any admin-provisioned IDs

#### Scenario: Feed with no assigned resources is an empty state, not an error

- **WHEN** the workspace feed download succeeds and returns no resources for the account
- **THEN** the AVD section renders the empty state and no error indicator

### Requirement: Feed token acquisition uses the AVD audience via silent acquisition

The provider SHALL obtain a token for audience `https://www.wvd.microsoft.com/.default` from the Auth/Account
Manager by silent acquisition immediately before every feed call (FR-4-AC-1), using the client identity selected by
the Stage 0 finding. The provider SHALL NOT cache tokens or schedule refresh from assumed lifetimes.

#### Scenario: Every feed call is preceded by a silent acquisition

- **WHEN** the provider performs feed discovery or a workspace feed download
- **THEN** a silent token acquisition for the owning account and the feed audience is issued immediately before the
  call, and the call carries the token it returned

#### Scenario: Feed rejects an issued token

- **WHEN** the feed returns `401` for a token that Entra issued successfully
- **THEN** the provider treats this as an identity-model failure — not expiry: the account does not enter
  ReauthRequired, the native method reports `Unavailable` with that reason, web launch remains offered, and the
  audience actually issued is logged (§9)

### Requirement: Feed parser is testable from checked-in fixtures without network

The feed parser SHALL be a pure function from response bytes to the typed resource model, exercisable in CI purely
from checked-in, redacted fixture files: one captured feed-discovery response and one captured workspace-feed
response (§13.3, §13.4). Fields not present in the Stage 1 schema capture SHALL be ignored by the parser.

#### Scenario: CI parses the captured fixtures offline

- **WHEN** the unit test suite runs with no network access
- **THEN** the parser produces the expected workspace/desktop/RemoteApp model from the checked-in discovery and
  workspace-feed fixtures

#### Scenario: Unknown fields in the feed are ignored

- **WHEN** a feed response contains a field absent from the captured schema
- **THEN** the parser ignores it and the resource model contains only fields the schema documents

### Requirement: Feed traffic honors proxy configuration

All feed traffic SHALL honor the environment's proxy configuration (`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` and
desktop proxy settings), and proxy failures SHALL be reported as proxy failures, distinguishable from plain network
unreachability (§5.9, §9).

#### Scenario: Proxy rejection is named as the cause

- **WHEN** the configured proxy refuses the feed connection
- **THEN** the error surface names the proxy as the cause and shows the configuration the app detected, and the
  condition is not reported as offline
