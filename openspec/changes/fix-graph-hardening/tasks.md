# Tasks: fix-graph-hardening

## 1. URL validation (F-01, HIGH)

- [ ] 1.1 In `graph_get_json`, before token acquisition: require `isinstance(url, str)`, `urlsplit` scheme ==
      `https`, hostname == `graph.microsoft.com` (exact); raise `GraphError` otherwise — `graph_client.py`
- [ ] 1.2 Tests: hostile nextLink host → no second HTTP call, no token acquired for it; `http://` scheme rejected;
      non-string nextLink rejected; lookalike hosts (`graph.microsoft.com.evil.example`) rejected —
      `tests/test_graph_client.py`, `tests/test_cloudpc_provider.py`

## 2. Typed malformed-payload handling (F-03)

- [ ] 2.1 Move `response.json()` inside the try in `graph_get_json`; `ValueError` → `GraphError` — `graph_client.py:227`
- [ ] 2.2 Wrap the provider parse loop so `KeyError`/`TypeError` from a malformed entry or non-list `value`
      becomes `Failed` with previous entries retained and a contract-change log line — `cloudpc_provider.py:246-277`
- [ ] 2.3 Tests: page with entry missing `id` → `Failed`, not `KeyError`; 200 non-JSON body → `GraphError`;
      `value` as dict → `Failed`

## 3. Retry contract (F-16, F-17)

- [ ] 3.1 Retry 502/503/504 in the existing backoff loop (same budget, same cap); 500 unchanged — `graph_client.py:276-280`
- [ ] 3.2 Clamp honored `Retry-After` to 300 s, logging the clamp — `graph_client.py:244-247`
- [ ] 3.3 Tests: 503 then 200 → success with one retry; 500 → immediate `GraphError`; 429 with `Retry-After: 3600`
      → sleep capped at 300; `Retry-After` as HTTP-date string → backoff fallback, no crash (also closes the F-22
      date-form gap for this module)

## 4. Spec sync (CLAUDE.md: apply amendments when accepted)

- [ ] 4.1 Amend spec.md §9's retry row: 5xx transients join 429; state the 300 s `Retry-After` ceiling
- [ ] 4.2 Note in spec.md §10.1 that the host-allowlist principle applies to Graph paging URLs (one sentence,
      cross-referencing this change)
- [ ] 4.3 Comment on the `WinOnLinuxCode` Linear issue for this change with the audit finding IDs it closes
      (F-01, F-03, F-16, F-17)
