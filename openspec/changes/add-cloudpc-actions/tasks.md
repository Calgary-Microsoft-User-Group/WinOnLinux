## 1. Service skeleton and flag

- [ ] 1.1 Define the `invokeAction(account, cloudPcId, action)` interface with the closed outcome enum
      (`Accepted`, `ConsentRequired`, `ContractError`, `Throttled`, `Failed(reason)`) mapped to §9 rows
- [ ] 1.2 Implement beta-API feature flag isolating endpoint/version construction in one module (§7.2);
      flag-off → actions disabled with reason
- [ ] 1.3 Silent token acquisition before every invocation (FR-4-AC-1), using the auth manager's per-account
      acquisition

## 2. Action invocations

- [ ] 2.1 Implement Restart (`reboot`), Rename, Troubleshoot against beta `POST /me/cloudPCs/{id}/...` (§7.1)
- [ ] 2.2 Implement Reprovision as a separate code path requiring a confirmation token from the UI; excluded
      from batching and retry wrappers; cancel returns before any network call (FR-5-AC-2, §10.6)
- [ ] 2.3 Response shape validation for every action; unexpected shape → `ContractError`, per-action disable
      with "Action unavailable — Microsoft API change", redacted logging (FR-5-AC-5, §10.7)
- [ ] 2.4 `429` handling: honor `Retry-After`, exponential backoff, no user-visible error unless persistent
      (§9) — never applied to reprovision

## 3. Gating

- [ ] 3.1 Admin capability evaluation from token role/`wids` claims, cached per account, re-evaluated on
      refresh; unknown → not capable (D-7, FR-5-AC-3)
- [ ] 3.2 State-gated enablement per §7.4: permit-list per action with the interim rule (unknown/unrecognized
      status → disabled with reason); expose disabled-reason strings for the UI
- [ ] 3.3 Post-action conflict suppression until the next refresh resolves the new state; provisional 60 s
      timeout pending G-19
- [ ] 3.4 National-cloud detection from the account's authority; disable FR-5 wholesale with explanatory
      message where Cloud PC Graph APIs are absent (FR-5-AC-6)

## 4. Completion observation

- [ ] 4.1 Immediate §7.3 refresh request after an accepted action (D-8); status chip update flows from
      refresh data (FR-5-AC-1)
- [ ] 4.2 Bounded-timeout handling: on expiry, re-read state and re-enable actions only after a clean refresh

## 5. Fixtures and tests

- [ ] 5.1 Fixtures per §13.3: accepted action response, mutated beta shape, `403` unconsented, `429` with
      `Retry-After`
- [ ] 5.2 Unit tests: shape-validation degradation (FR-5-AC-5), reprovision no-call-on-cancel and
      no-auto-retry (FR-5-AC-2), capability gating both ways (FR-5-AC-3), unknown-status disable (§7.4)
- [ ] 5.3 Integration tests (on demand, live tenant): FR-5-AC-1 action round-trips, FR-5-AC-3 with admin and
      plain accounts, FR-5-AC-4 in an unconsented tenant (§11.2)
- [ ] 5.4 Document that G-19 (status×action matrix, tooltip strings, timeout value) supersedes the interim
      gating rule when it lands; link the G-19 backlog item
