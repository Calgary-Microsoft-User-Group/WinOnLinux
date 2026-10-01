# cloudpc-enumeration — observable refresh outcomes

Adds the result-notification surface spec §4.4's offline/error/chip states require (audit 2026-09-22 finding F-07;
spec.md §4.3, §4.4, §7.3).

## ADDED Requirements

### Requirement: Every refresh outcome is observable

The provider SHALL expose `add_result_listener` / `remove_result_listener`; registered callbacks SHALL be invoked
with `(home_account_id, result)` for **every** refresh outcome — manual, post-action, and background poll ticks;
`Enumerated`, `Empty`, `NoLicence`, `ConsentRequired`, and `Failed` alike. `Failed` SHALL continue to leave the
stored last-known-good result untouched (FR-1-AC-4); the listener is the channel that carries failures.

#### Scenario: Failing poll ticks drive the offline banner

- **WHEN** the network drops and a 60 s poll tick returns `Failed`
- **THEN** listeners receive the `Failed` (with previous entries) on that tick — the §4.4 offline banner and
  greyed list need no UI-side polling timer

#### Scenario: Status chip updates on the next refresh

- **WHEN** a poll tick returns `Enumerated` with a changed Cloud PC status
- **THEN** listeners receive the new result, satisfying §4.3's chip-updates-on-next-refresh without a second timer
