# cloudpc-enumeration — per-account enumeration state

Closes audit finding F-04 (2026-09-22): enumeration results must respect FR-3's per-account isolation across
account switches.

## ADDED Requirements

### Requirement: Enumeration state is per-account

The provider SHALL key its retained results by `home_account_id`. A `Failed` result's `previous_entries` SHALL
only ever contain entries previously enumerated for the same account, and a reader asking for an account's last
result SHALL never receive another account's data (FR-3).

#### Scenario: First refresh after a switch fails

- **WHEN** account A has enumerated successfully, the user switches to account B, and B's first refresh fails
- **THEN** the `Failed` result carries no previous entries (B has none) and A's entries are not presented as B's

#### Scenario: Switching back

- **WHEN** the user switches back to account A after the above
- **THEN** A's previously enumerated list is still available as A's last result (FR-1-AC-4 preserved per account)
