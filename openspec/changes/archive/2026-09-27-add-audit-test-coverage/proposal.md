# Proposal: add-audit-test-coverage

## Why

The 2026-09-22 audit's Part D (`codeaudit/2026-09-22.md`) verified 13 of 15 deliberate controls implemented, but
several exist only as docstring assertions with no pinning test — and the audit's ground rule (a control asserted
in a docstring but not enforced by a test is a finding) applies. The loopback listener's 16 KiB request cap and
slowloris behavior are wholly untested (F-10, MEDIUM); a regression to `shell=True` in the FreeRDP probe would
pass the current suite; redaction is never exercised against a third-party logger; the `Retry-After` HTTP-date
fallback, bind-host/ephemeral-port properties, second-process statelessness, authority non-overridability, and the
placeholder-client-id WARNING are all unpinned (F-22, F-23). On the supply-chain side, dependency floors admit
`requests` versions with known CVEs — including CVE-2024-47081 on the `trust_env` proxy path this app relies on —
with no constraints strategy for the future Flatpak manifest to consume (F-12). And CI tests only Python 3.11
while `requires-python >=3.11` is unbounded — which is how the F-09 Python 3.12 failure stayed invisible.

## What Changes

- **Loopback robustness tests** (F-10): oversized (>16 KiB) request line → rejected without consuming the accept
  slot or deadline; stalled connection freed at the 10 s read timeout while a concurrent real redirect succeeds.
- **Control-pinning tests** (F-22, F-23): probe invocation asserted as `[binary, "/version"]` with no `shell`
  kwarg; redaction exercised via a `urllib3`-named logger; `Retry-After` HTTP-date → backoff fallback; loopback
  `getsockname()` pins `127.0.0.1` and distinct ephemeral ports; a headless test asserts
  `WinOnLinuxApplication.__init__` constructs nothing stateful (control 15's automatable half); authority/tenant
  shown constructor-only; placeholder-client-id WARNING fires exactly once across two `start()` calls.
- **Dependency floors and constraints** (F-12): raise `requests` floor past the known-CVE range (≥2.32.4); add a
  `pip-compile`-generated constraints file that CI installs from and the future Flatpak manifest consumes as its
  pinned module list; add `pip-audit` to CI. Recorded as a new §14 decision (constraints strategy) with rationale
  and revisit trigger.
- **CI Python matrix** (F-09's verification half): CI runs the suite on 3.11, 3.12, and 3.13, turning the
  asyncio-private-API and loop-affinity assumptions into continuously verified facts.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `auth-accounts`: loopback oversized/stalled-connection behavior gets scenarios (behavior already implemented).
- `app-foundation`: CI version matrix; second-process statelessness pin.
- `packaging`: dependency floor and constraints/lock strategy.

## Impact

- Code: tests only, plus `pyproject.toml`, a new `constraints.txt`, and `.github/workflows/ci.yml`.
- Spec: new §14 decision for the constraints strategy; §12's assumed-facts additions (Retry-After form, asyncio
  private-API version range) marked as now CI-verified where the matrix covers them.
- Closes audit findings F-10, F-12, F-22, F-23 and the verification half of F-09. Depends on
  `fix-shutdown-loop-hygiene` for a green 3.12 run (its serializer fix); everything else is independent.
- File and link a `WinOnLinuxCode` Linear issue per repo convention.
