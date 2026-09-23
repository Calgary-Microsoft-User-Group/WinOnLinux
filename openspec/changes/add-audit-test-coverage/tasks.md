# Tasks: add-audit-test-coverage

## 1. Loopback robustness tests (F-10)

- [ ] 1.1 Test: >16 KiB request line → invalid-request response; accept slot not consumed; subsequent correct
      redirect succeeds — `tests/test_auth_loopback.py`
- [ ] 1.2 Test: stalled connection (no complete request line) freed at the read timeout while a concurrent correct
      redirect completes within the deadline (override the timeout constant, real sockets)

## 2. Control-pinning tests (F-22, F-23)

- [ ] 2.1 Probe: assert recorded invocation is `[binary, "/version"]` and no `shell` kwarg —
      `tests/test_freerdp_probe.py`
- [ ] 2.2 Redaction via third-party logger: token logged through `logging.getLogger("urllib3.connectionpool")` is
      redacted — `tests/test_logging_setup.py`
- [ ] 2.3 Graph: `Retry-After: <HTTP-date>` → backoff fallback, no crash — `tests/test_graph_client.py`
      (parametrize the existing header tests)
- [ ] 2.4 Loopback socket properties: `getsockname()` pins `127.0.0.1`; two listeners get distinct non-privileged
      ports — `tests/test_auth_loopback.py`
- [ ] 2.5 New `tests/test_app.py`: constructor-purity test with the repo's GTK stubs — no cache/store/listener/
      registry constructed before `_first_activate` (control 15's automatable half)
- [ ] 2.6 Auth manager: authority derives only from constructor args (no env/state read); placeholder-client-id
      WARNING fires exactly once across two `start()` calls — `tests/test_auth_manager.py`

## 3. Dependency floors and constraints (F-12)

- [ ] 3.1 `pyproject.toml`: `requests>=2.32.4`; regenerate metadata
- [ ] 3.2 Add `pip-compile`-generated `constraints.txt`; CI installs with `-c constraints.txt`
- [ ] 3.3 Add `pip-audit` CI step (report-only initially; task to promote to blocking once quiet)
- [ ] 3.4 Record the constraints strategy as spec.md §14 D-23 with rationale and revisit trigger; note in
      `add-flatpak-packaging`'s proposal that the manifest consumes `constraints.txt`

## 4. CI Python matrix (F-09 verification half)

- [ ] 4.1 `.github/workflows/ci.yml`: matrix {3.11, 3.12, 3.13} (3.12/3.13 green requires
      `fix-shutdown-loop-hygiene`'s serializer fix — sequence accordingly)
- [ ] 4.2 spec.md §12 assumed-facts additions: asyncio private-API tested range now pointed at the CI matrix;
      Retry-After integer-form assumption marked as pinned by 2.3's test

## 5. Sync

- [ ] 5.1 Comment on the `WinOnLinuxCode` Linear issue with the findings closed (F-10, F-12, F-22, F-23, F-09
      verification half); add a blocking relation on `fix-shutdown-loop-hygiene` for the matrix task
