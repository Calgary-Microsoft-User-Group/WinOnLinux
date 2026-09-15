## Why

FR-2 requires every resource to offer a web launch method, and Phase 0 ships web-only (§11.1) — the web launcher
is therefore the first connection path users can actually exercise, and the fallback surface every native-launch
failure must offer (FR-2-AC-5). All of it sits on supported, public API surface (§2.2), so it carries the least
risk of anything on the roadmap and can land before any native-path gate resolves.

## What Changes

- New **Web launcher** component (§5.1): builds direct-launch URLs per §5.3 and opens the system browser.
- Windows 365 URL composition: `https://windows.cloud.microsoft/webclient/ent/<CloudPc.Id>` using the Graph `id`
  retained at enumeration (FR-1-AC-2), with `?tenant=<tenantID>` before the fragment and `#loginHint=<active UPN>`
  as the final component (FR-2-AC-3).
- Preference for the Microsoft-issued `cloudPcLaunchUrl` from Graph beta `retrieveCloudPcLaunchDetail` when
  available, falling back to the constructed `ent/` URL. The deprecated `getCloudPcLaunchInfo` is never called
  (hard stop 2026-10-30, §12 risk 7).
- AVD URL composition: `https://windows.cloud.microsoft/webclient/avd/<workspaceID>/<resourceID>` for
  admin-provisioned IDs (Phase 0, §11.1); entries without IDs show the web method disabled with the specific
  reason (FR-2-AC-1).
- Browser spawn via the system browser — the OpenURI portal under Flatpak, where browser choice is the portal's
  (§5.8, §10.5).
- Failure handling per §9: browser fails to spawn → error toast offering the URL for manual copy.
- Second-RemoteApp-tab warning when launching another RemoteApp from the same host pool (§12 risk 9).
- UPN is placed only in the URL fragment, never in the query — the documented §10.5 trade-off.

## Capabilities

### New Capabilities

- `web-launch`: direct-launch URL composition and system-browser handoff for Windows 365 and AVD resources —
  the FR-2 web connection method.

### Modified Capabilities

<!-- none — no existing capability specs yet -->

## Impact

- New module: web launcher (URL builder + browser spawner). Pure URL composition is CI-testable (§13.2 unit level).
- Depends on: `add-auth-account-manager` (active-account UPN/tenant, silent token for the launch-detail call,
  FR-4-AC-1) and `add-cloudpc-enumeration` (Graph `id` per Cloud PC).
- Consumed by: `add-ui-shell` (Connect split button, FR-2 method selection and fallback surface).
- Graph beta dependency: `retrieveCloudPcLaunchDetail` only, behind the §7.2 beta feature flag; constructed URLs
  are the degradation path, so a beta contract change cannot break web launch.
