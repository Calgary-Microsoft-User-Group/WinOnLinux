# packaging — dependency floors and constraints strategy

Closes the supply-chain gap in the applied dependency metadata (audit 2026-09-22 finding F-12; D-3, spec.md
§10.1). Owned here because the constraints file is what the Flatpak manifest will consume; the floor change lands
now, before packaging work begins.

## ADDED Requirements

### Requirement: Dependency floors exclude known-vulnerable versions

Declared dependency floors SHALL exclude versions with known applicable CVEs; in particular `requests` SHALL floor
at ≥ 2.32.4 (CVE-2023-32681, CVE-2024-35195, CVE-2024-47081 — the last on the `trust_env` proxy path §5.9
relies on). Floors SHALL be revisited when CI's dependency audit reports a new applicable CVE.

#### Scenario: Vulnerable resolution refused

- **WHEN** the project is installed in an environment resolving `requests` below the floor
- **THEN** installation fails rather than silently shipping the vulnerable version

### Requirement: One generated constraints file pins CI and packaging

A `pip-compile`-generated constraints file SHALL pin the full resolved dependency set; CI SHALL install with it,
and the Flatpak manifest (add-flatpak-packaging) SHALL consume the same file as its pinned module list, so the
tested set and the shipped set are one artifact. CI SHALL run a dependency CVE audit (`pip-audit`) against the
pinned set.

#### Scenario: Tested equals shipped

- **WHEN** the Flatpak manifest is generated
- **THEN** its Python module pins come from the same constraints file CI tested, with no independently chosen
  versions
