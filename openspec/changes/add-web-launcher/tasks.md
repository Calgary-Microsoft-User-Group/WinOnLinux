## 1. URL composition module

- [ ] 1.1 Implement `buildWebLaunchUrl(resource, account)` as a pure function: `ent/` form for Cloud PCs from
      the retained Graph `id`, `avd/<workspaceID>/<resourceID>` form for AVD, `?tenant=` before fragment,
      `#loginHint=<active UPN>` always last (§5.3, FR-2-AC-3)
- [ ] 1.2 Include `?tenant=` only when the account's home tenant differs from the browser default; assert UPN
      appears in the fragment only (§10.5)
- [ ] 1.3 Unit tests for FR-2-AC-1…AC-3 composition rules: ordering, encoding, AVD/ent forms, tenant
      inclusion/omission (§11.2 U-level, CI per §13.4)

## 2. Launch-detail fast path

- [ ] 2.1 Implement `retrieveCloudPcLaunchDetail` client call behind the §7.2 beta feature flag, preceded by
      silent token acquisition (FR-4-AC-1)
- [ ] 2.2 Prefer the issued `cloudPcLaunchUrl`; on any error, unexpected shape, or flag-off, fall back to the
      constructed URL silently and log for triage; never reference `getCloudPcLaunchInfo`
- [ ] 2.3 Fixture tests: valid launch-detail response, mutated shape, error response — all yield a working
      launch URL (§13.3)

## 3. Browser handoff

- [ ] 3.1 Implement system-browser spawn (OpenURI portal under Flatpak, desktop default otherwise)
- [ ] 3.2 Spawn-failure path: error toast with the URL offered for manual copy (§9)
- [ ] 3.3 Launch feedback hook for the UI shell: spinner until the browser is spawned (§4.2)

## 4. AVD availability and RemoteApp warning

- [ ] 4.1 Disabled-with-reason state for AVD entries lacking admin-provisioned IDs: "Web launch unavailable:
      workspace/resource ID unknown" (FR-2-AC-1)
- [ ] 4.2 Per-host-pool, per-session RemoteApp launch tracking and the second-tab disconnect warning
      (§12 risk 9)

## 5. Verification and documentation

- [ ] 5.1 Manual test: web launch lands on the active account without an account picker, from two different
      accounts (FR-2-AC-3, §11.2 M-level)
- [ ] 5.2 Manual test against a live tenant: whether tenant/loginHint components may be appended to the
      Microsoft-issued `cloudPcLaunchUrl`; record the finding and amend §5.3 (design Open Question)
- [ ] 5.3 Document the UPN-in-browser-history trade-off (§10.5) in user documentation
