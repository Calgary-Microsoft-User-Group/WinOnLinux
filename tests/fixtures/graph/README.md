# Graph fixtures (add-cloudpc-enumeration, tasks.md 5.1)

Synthetic, redacted JSON fixtures for `winonlinux.cloudpc_provider` tests. No real tenant data --
every ID, UPN, and request-id here is a made-up placeholder.

- `cloudpcs_page1.json` / `cloudpcs_page2.json` -- a two-page `/me/cloudPCs` response. Page 1
  carries `@odata.nextLink` pointing at a fixture-only placeholder URL (never a real, resolvable
  Graph URL -- tests stub `graph_client.graph_get_json` to recognize this exact string and hand
  back page 2 rather than actually requesting it); page 2 has no `@odata.nextLink`, ending the
  page sequence. Together they carry 3 CloudPC entries with 3 distinct `status` values
  (`provisioned`, `provisioning`, `inGracePeriod`).
- `cloudpcs_404.json` -- the no-licence error envelope (`GET /me/cloudPCs` -> HTTP 404).
- `cloudpcs_403.json` -- the consent-required error envelope (`GET /me/cloudPCs` -> HTTP 403,
  the G-42 fix's trigger case).
- `cloudpcs_429.json` -- a throttling error envelope (`GET /me/cloudPCs` -> HTTP 429). The
  `Retry-After` value itself is an HTTP **header**, not part of this JSON body, so it is not
  represented here -- tests that need a specific `Retry-After` value set it directly wherever
  they construct the simulated response/exception.

## ASSUMED, not verified against a real tenant (CLAUDE.md "verify, don't assert")

- The exact field set and casing on a `cloudPC` resource (`displayName`, `status`,
  `provisioningType`, `imageDisplayName`, `managedDeviceName`, `servicePlanName`,
  `userPrincipalName`) is based on Microsoft's **documented** `cloudPC` resource type shape, not
  captured from a live `/me/cloudPCs` response. Field presence/casing should be re-verified against
  a real tenant response per tasks.md section 6 ("Integration (live tenant, on-demand)").
- The Graph error envelope shape (`{"error": {"code", "message", "innerError": {...}}}`) is the
  standard, documented Graph error response shape and is assumed to apply unchanged to
  `/me/cloudPCs`'s 404/403/429 cases specifically -- also unverified against a real tenant.
- `status` values used here (`provisioned`, `provisioning`, `inGracePeriod`) are documented
  `cloudPC.status` enum members; this fixture set does not attempt to exercise every possible value,
  only enough distinct ones to prove the raw string passes through unmodified (G-19 is a separate
  change's job -- see `cloudpc_provider.py`'s module docstring).
