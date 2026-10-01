"""Cloud PC Management Action service (add-cloudpc-actions change; FR-5, spec.md §7.1/§7.2).

Self-service actions exist only on Graph **beta** ``/me`` paths, a surface Microsoft marks
"production use is not supported" (§7.2) -- so this module is as much containment as
invocation: one feature flag isolates version/endpoint selection, every response is
shape-validated, and a contract change degrades exactly one action ("Action unavailable --
Microsoft API change", FR-5-AC-5) rather than touching enumeration or either connect method.

The public surface the UI binds to:

- :meth:`CloudPcActionService.invoke_action` / :meth:`~CloudPcActionService.invoke_reprovision`
  -- returning the CLOSED outcome family (:class:`Accepted`, :class:`ConsentRequired`,
  :class:`ContractError`, :class:`Throttled`, :class:`Failed`), the §9 rows as values so the UI
  cannot invent handling.
- :meth:`CloudPcActionService.action_gate` -- (enabled, reason) per action, folding the beta
  flag (§7.2), national-cloud availability (FR-5-AC-6), and per-session contract disables
  (FR-5-AC-5) into the §7.4 disabled-with-reason rendering the shell already does.
- :meth:`CloudPcActionService.evaluate_admin_capability` -- D-7's token role/``wids``-claim
  heuristic, cached per token, unknown → not capable (FR-5-AC-3).

Reprovision is a structurally separate code path (FR-5-AC-2, §10.6): its own method, a
``confirmed=True`` argument only the UI's typed/checked dialog produces, ``max_retries=0`` so
nothing -- not a 429, not a transient 5xx -- is ever retried automatically, and a refusal
before any network call when confirmation is absent.

Completion is observed by polling (D-8): an accepted action schedules an immediate §7.3
refresh via ``CloudPcProvider.refresh_after_action``; the UI's pending-transition suppression
clears on that refresh or on :data:`POST_ACTION_REFRESH_TIMEOUT_SECONDS` (provisionally 60 s,
superseded when G-19 lands).

ASSUMED, per the repo's conventions (all flagged for Phase 0 integration verification, §11.2):
that the beta ``/me`` actions behave as documented (endpoint matrix is July 2026, §7.1); the
admin role-template GUIDs in :data:`_ADMIN_ROLE_TEMPLATE_IDS` (D-7 names the mechanism, not the
id set); that ``wids`` claims appear in the access token this client can decode (the JWT is
inspected WITHOUT signature verification -- fine for client-side visibility gating, never for
authorization, which Graph enforces server-side); and that authority-host detection suffices
for the 21Vianet case while DOD (which shares ``login.microsoftonline.us`` with GCC-High,
where Cloud PC APIs DO exist) cannot be distinguished by authority alone -- recorded here
rather than guessed.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass
from urllib.parse import quote

from winonlinux import graph_client

__all__ = [
    "ActionOutcome",
    "Accepted",
    "ConsentRequired",
    "ContractError",
    "Throttled",
    "Failed",
    "CloudPcActionService",
    "SELF_SERVICE_ACTIONS",
    "ADMIN_VISIBILITY_ACTIONS",
    "CONTRACT_ERROR_REASON",
    "POST_ACTION_REFRESH_TIMEOUT_SECONDS",
]

logger = logging.getLogger(__name__)

#: §4.3's self-service actions mapped to their §7.1 beta endpoint segments -- THE one place
#: version/endpoint selection lives (§7.2): when Microsoft promotes the /me paths to v1.0,
#: :data:`_GRAPH_VERSION` is the whole switch.
_ACTION_ENDPOINTS: dict[str, str] = {
    "restart": "reboot",
    "rename": "rename",
    "troubleshoot": "troubleshoot",
    "reprovision": "reprovision",
}
_GRAPH_VERSION = "beta"

SELF_SERVICE_ACTIONS = tuple(_ACTION_ENDPOINTS)

#: Visibility-gated only (D-7, FR-5-AC-3) -- no /me path exists (§7.1) and this change ships
#: no admin-path invocation; the UI renders these solely when admin capability is affirmed.
ADMIN_VISIBILITY_ACTIONS = ("restore", "resize")

#: FR-5-AC-5's exact wording, used both as the per-action disable reason and the outcome text.
CONTRACT_ERROR_REASON = "Action unavailable — Microsoft API change"

#: D-8's bounded wait: after an accepted action, conflicting actions stay suppressed until a
#: clean refresh or this timeout, provisionally aligned to the §7.3 poll interval. G-19 owns
#: the final value.
POST_ACTION_REFRESH_TIMEOUT_SECONDS = 60.0

#: Entra role-template ids treated as admin-capable for Restore/Resize visibility (D-7).
#: ASSUMED (see module docstring): Global Administrator, Intune Administrator, and Windows 365
#: Administrator. False negatives are D-7's accepted cost; capability evaluations are logged
#: (ids only, redaction-safe) so their frequency is measurable per D-7's revisit trigger.
_ADMIN_ROLE_TEMPLATE_IDS = frozenset(
    {
        "62e90394-69f5-4237-9190-012177145e10",  # Global Administrator
        "3a2c62db-5318-420d-8d74-23affee5d9d5",  # Intune Administrator
        "11451d60-acb2-45eb-a7d6-43d0f0125c13",  # Windows 365 Administrator
    }
)

#: Authority hosts whose clouds ship no Cloud PC Graph APIs (§7.2): FR-5 disables wholesale
#: there (FR-5-AC-6). 21Vianet is detectable by authority; DOD is NOT (shares the .us
#: authority with GCC-High) -- see the module docstring.
_CLOUDS_WITHOUT_CLOUDPC_APIS = frozenset(
    {"login.chinacloudapi.cn", "login.partner.microsoftonline.cn"}
)

FR5_NATIONAL_CLOUD_REASON = (
    "Cloud PC management actions are not available in this organization's cloud environment."
)
BETA_FLAG_OFF_REASON = (
    "Cloud PC management actions are disabled in this build (Microsoft's preview API is "
    "turned off)."
)


# --- the closed outcome family (§9 rows as values) ----------------------------------------------


class ActionOutcome:
    """Marker base; the UI dispatches on isinstance across exactly these five shapes."""


@dataclass(frozen=True)
class Accepted(ActionOutcome):
    """Graph accepted the action (204). Completion surfaces via the §7.3 refresh (D-8)."""

    action: str
    user_message: str = "Requested. The status updates on the next refresh."


@dataclass(frozen=True)
class ConsentRequired(ActionOutcome):
    """403: tenant admin consent for ``CloudPC.ReadWrite.All`` is missing (FR-5-AC-4)."""

    admin_consent_url: str | None
    user_message: str = (
        "An administrator of this organization must approve this app's management "
        "permissions before actions can be used."
    )


@dataclass(frozen=True)
class ContractError(ActionOutcome):
    """The beta response failed shape validation -- this action is disabled for the session
    (FR-5-AC-5); enumeration and both connect methods are untouched."""

    action: str
    user_message: str = CONTRACT_ERROR_REASON


@dataclass(frozen=True)
class Throttled(ActionOutcome):
    """429 persisted past the retry budget (or, for reprovision, occurred at all -- that path
    never retries)."""

    retry_after_seconds: float | None
    user_message: str = "Microsoft's service is busy. Try again in a moment."


@dataclass(frozen=True)
class Failed(ActionOutcome):
    """Transport or unclassified failure; ``user_message`` carries the §9 wording."""

    user_message: str


# --- token-claim helpers (D-7) --------------------------------------------------------------------


def _decode_jwt_claims(token: str) -> dict | None:
    """Best-effort decode of a JWT's payload segment WITHOUT signature verification.

    Used only for client-side visibility gating (D-7) -- authorization is Graph's job. Any
    malformation returns ``None`` (→ not capable, FR-5-AC-3's fail-closed direction). The
    token value itself is never logged (§10.7).
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


# --- the service ------------------------------------------------------------------------------


class CloudPcActionService:
    """FR-5 invocation, gating, and beta containment. One instance per application."""

    def __init__(
        self,
        *,
        auth_manager,
        cloudpc_provider,
        task_registry,
        beta_actions_enabled: bool = True,
        authority_host: str = "login.microsoftonline.com",
        scopes: tuple[str, ...] = ("CloudPC.ReadWrite.All",),
    ) -> None:
        self._auth = auth_manager
        self._provider = cloudpc_provider
        self._task_registry = task_registry
        # §7.2's containment flag. Defaults ON: FR-5 ships in Phase 0 (§11.1) and the flag
        # exists to turn the beta surface OFF in one place when it misbehaves, not to hide the
        # feature by default.
        self._beta_enabled = beta_actions_enabled
        self._authority_host = authority_host
        self._scopes = tuple(scopes)
        # FR-5-AC-5: actions disabled for the session after a contract error -- per action,
        # never wholesale.
        self._contract_disabled: set[str] = set()
        # D-7 capability cache: home_account_id -> (token, capable). Re-evaluated whenever the
        # silently-acquired token string changes (i.e. on refresh).
        self._capability_cache: dict[str, tuple[str, bool]] = {}

    # -- gating (§7.4 inputs for ui_models.cloudpc_actions) --------------------------------------

    def service_disabled_reason(self) -> str | None:
        """The wholesale-disable reason (flag off, §7.2; national cloud, FR-5-AC-6), or None."""
        if not self._beta_enabled:
            return BETA_FLAG_OFF_REASON
        if self._authority_host in _CLOUDS_WITHOUT_CLOUDPC_APIS:
            return FR5_NATIONAL_CLOUD_REASON
        return None

    def action_gate(self, action: str) -> tuple[bool, str | None]:
        """(enabled, reason) for one action -- the UI folds this into its §7.4 Availability."""
        wholesale = self.service_disabled_reason()
        if wholesale is not None:
            return False, wholesale
        if action in self._contract_disabled:
            return False, CONTRACT_ERROR_REASON
        if action in ADMIN_VISIBILITY_ACTIONS:
            # Visible only when admin-capable (the UI's job to gate presence, FR-5-AC-3), but
            # never invocable in this change: no /me path exists (§7.1) and admin-path
            # invocation is deliberately out of scope.
            return False, "Restore and Resize are not yet invocable from this app"
        if action not in _ACTION_ENDPOINTS:
            return False, f"Unknown action {action!r}"
        return True, None

    async def evaluate_admin_capability(self, home_account_id: str) -> bool:
        """D-7: admin capability from the access token's role/``wids`` claims, cached per
        token, unknown/absent/undecodable → not capable (FR-5-AC-3)."""
        try:
            token = await self._auth.acquire_token_silently(
                home_account_id, ["CloudPC.Read.All"]
            )
        except Exception:  # noqa: BLE001 - capability is a visibility nicety, never an error
            logger.info(
                "cloudpc_actions: capability evaluation for account %s skipped "
                "(no token available)",
                home_account_id,
            )
            return False

        cached = self._capability_cache.get(home_account_id)
        if cached is not None and cached[0] == token:
            return cached[1]

        claims = _decode_jwt_claims(token)
        wids = claims.get("wids") if claims else None
        capable = isinstance(wids, list) and bool(
            _ADMIN_ROLE_TEMPLATE_IDS.intersection(w for w in wids if isinstance(w, str))
        )
        self._capability_cache[home_account_id] = (token, capable)
        logger.info(
            "cloudpc_actions: account %s admin capability evaluated: %s (D-7; ids-only log)",
            home_account_id,
            capable,
        )
        return capable

    # -- invocation --------------------------------------------------------------------------------

    async def invoke_action(
        self,
        home_account_id: str,
        cloudpc_id: str,
        action: str,
        *,
        payload: dict | None = None,
    ) -> ActionOutcome:
        """Invoke Restart/Rename/Troubleshoot (FR-5-AC-1). Reprovision is refused here -- it
        has its own structurally separate path (:meth:`invoke_reprovision`, FR-5-AC-2)."""
        if action == "reprovision":
            raise ValueError(
                "reprovision goes through invoke_reprovision (its own confirmed, "
                "never-retried path) -- not the shared action path"
            )
        if action not in _ACTION_ENDPOINTS:
            raise ValueError(f"unknown self-service action {action!r}")
        return await self._post_action(
            home_account_id, cloudpc_id, action, payload=payload, max_retries=5
        )

    async def invoke_reprovision(
        self, home_account_id: str, cloudpc_id: str, *, confirmed: bool
    ) -> ActionOutcome:
        """The destructive path (FR-5-AC-2, §10.6): requires the UI's explicit confirmation
        token, refuses BEFORE any network call without it, and never retries -- a transient
        429/5xx surfaces immediately rather than re-issuing a wipe-and-rebuild."""
        if confirmed is not True:
            raise ValueError(
                "invoke_reprovision requires confirmed=True from the typed/checked dialog; "
                "an unconfirmed call is a caller bug, refused before any network I/O"
            )
        return await self._post_action(
            home_account_id, cloudpc_id, "reprovision", payload=None, max_retries=0
        )

    async def _post_action(
        self,
        home_account_id: str,
        cloudpc_id: str,
        action: str,
        *,
        payload: dict | None,
        max_retries: int,
    ) -> ActionOutcome:
        enabled, reason = self.action_gate(action)
        if not enabled:
            # Defensive: a well-behaved UI never calls a gated-off action (§7.4 renders it
            # disabled), but the refusal must not depend on UI discipline.
            return Failed(user_message=reason or "This action is unavailable.")

        url = (
            f"https://graph.microsoft.com/{_GRAPH_VERSION}/me/cloudPCs/"
            f"{quote(cloudpc_id, safe='')}/{_ACTION_ENDPOINTS[action]}"
        )

        try:
            response = await graph_client.graph_post_json(
                url,
                auth_manager=self._auth,
                home_account_id=home_account_id,
                scopes=list(self._scopes),
                json_body=payload,
                max_retries=max_retries,
            )
        except graph_client.GraphConsentRequired as exc:
            logger.warning(
                "cloudpc_actions: %s for account %s needs tenant admin consent (403)",
                action,
                home_account_id,
            )
            return ConsentRequired(admin_consent_url=exc.admin_consent_url)
        except graph_client.GraphThrottled as exc:
            return Throttled(retry_after_seconds=exc.retry_after_seconds)
        except (graph_client.GraphNotFound, graph_client.GraphError) as exc:
            if isinstance(exc, graph_client.GraphNotFound) or (
                exc.status_code is not None and 400 <= exc.status_code < 500
            ):
                # A 4xx on a documented beta action path is the contract-change signature
                # (endpoint moved/removed/reshaped): disable THIS action for the session,
                # leave everything else working (FR-5-AC-5). A 404 could also mean the Cloud
                # PC itself is gone -- the next refresh removes that entry, so the
                # session-scoped disable costs little even when this reading is wrong.
                if not isinstance(exc, graph_client.GraphProxyError):
                    self._contract_disabled.add(action)
                    logger.error(
                        "cloudpc_actions: %s response failed contract expectations "
                        "(status=%s); action disabled for this session. Body (redacted "
                        "structurally by logging): %r",
                        action,
                        exc.status_code,
                        exc.response_body,
                    )
                    return ContractError(action=action)
            user_message = getattr(
                exc, "user_message", "The action could not be completed. Try again."
            )
            if isinstance(exc, graph_client.GraphProxyError):
                user_message = (
                    "A network proxy blocked the connection to Microsoft's service, so the "
                    "action was not sent."
                )
            elif isinstance(exc, graph_client.GraphNetworkError):
                user_message = "You appear to be offline. The action was not sent."
            return Failed(user_message=user_message)

        # Success is 204 No Content (returned as {}). A 2xx carrying an unexpected non-empty
        # body is shape-validation failure -> ContractError (design.md: shape validation, not
        # status codes alone).
        if response != {}:
            self._contract_disabled.add(action)
            logger.error(
                "cloudpc_actions: %s returned 2xx with an unexpected body shape; action "
                "disabled for this session (FR-5-AC-5). Keys seen: %s",
                action,
                sorted(response) if isinstance(response, dict) else type(response).__name__,
            )
            return ContractError(action=action)

        # D-8: completion observes the existing refresh -- request one immediately, scheduled
        # under the account's task group so an account switch cancels it.
        self._task_registry.get_or_create_group(home_account_id).create_task(
            self._provider.refresh_after_action(home_account_id),
            name=f"post-action-refresh-{action}",
        )
        logger.info(
            "cloudpc_actions: %s accepted for account %s; immediate refresh requested",
            action,
            home_account_id,
        )
        return Accepted(action=action)
