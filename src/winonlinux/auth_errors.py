"""MSAL error-result classification (tasks.md 3.2; spec.md §6.4, §6.5, §9).

This module is a pure, synchronous classifier: it takes the ``dict`` shape MSAL returns from a
failed ``acquire_token_silent_with_error`` / ``acquire_token_by_auth_code_flow`` call and turns it
into a :class:`ClassifiedMsalError` that :mod:`auth_state` (a separate module, built in parallel)
drives its per-account state machine from.

Logging discipline (spec.md §10.7): ``raw_error`` and ``raw_error_description`` are MSAL error
*codes and descriptions* (e.g. ``"invalid_grant"``, ``"AADSTS70008: ..."``) — never token, code, or
claims material — so they may be logged freely, including at DEBUG. This module never reads or
touches a token, authorization code, PKCE verifier, or client secret; there is nothing here that
falls under the RdpwContent-style redaction convention in :mod:`winonlinux.logging_setup`. Callers
that log a ``ClassifiedMsalError`` MUST still avoid logging its ``claims`` field's *contents*
verbatim in any context wider than what FR-4-AC-4 requires (carrying it into the interactive
re-auth request) — treat ``claims`` as sensitive-shaped even though it is not a token, since it can
carry tenant Conditional Access policy detail. This module itself never logs anything.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from urllib.parse import quote

__all__ = [
    "MsalErrorCategory",
    "ClassifiedMsalError",
    "classify_msal_error",
    "compose_admin_consent_url",
]


class MsalErrorCategory(enum.Enum):
    """Classification buckets for a failed MSAL acquisition result (spec.md §6.4/§6.5)."""

    INVALID_GRANT = "invalid_grant"
    CLAIMS_CHALLENGE = "claims_challenge"
    NETWORK_ERROR = "network_error"
    DEVICE_CA_BLOCKED = "device_ca_blocked"
    CONSENT_REQUIRED = "consent_required"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassifiedMsalError:
    """Result of classifying one MSAL error-result dict.

    ``claims`` is set only for :attr:`MsalErrorCategory.CLAIMS_CHALLENGE`, verbatim from the MSAL
    result's ``"claims"`` key — callers (per FR-4-AC-4) must carry it unmodified into the next
    interactive re-authentication.

    ``admin_consent_url`` is set only for :attr:`MsalErrorCategory.CONSENT_REQUIRED`, and only when
    :func:`classify_msal_error` was given enough information (``tenant``, ``client_id``,
    ``redirect_uri``) to compose it; otherwise it is ``None`` and the caller is responsible for
    composing/obtaining it another way before presenting the guided admin-consent flow (§9).

    ``raw_error`` / ``raw_error_description`` preserve the input dict's ``"error"`` /
    ``"error_description"`` values verbatim, for logging and diagnostics — these are MSAL error
    codes and human-readable descriptions, not token material, so no redaction is required.
    """

    category: MsalErrorCategory
    claims: str | None = None
    admin_consent_url: str | None = None
    raw_error: str | None = None
    raw_error_description: str | None = None


# ASSUMED pending Stage 0 confirmation (spec.md §12 risk 14, §6.5): the exact AADSTS device-state
# code family. 53000/53001/530003 are the commonly documented device-state / Conditional Access
# codes ("device is not compliant", "device is not domain joined", "device is disabled"), but the
# authoritative set for this tenant/config is NOT yet verified against a real Entra tenant. Do not
# treat this set as settled fact -- revisit at Stage 0 (tasks.md 8.2).
_DEVICE_CA_BLOCKED_CODES = frozenset({53000, 53001, 530003})
_DEVICE_CA_BLOCKED_SUBSTRINGS = ("AADSTS53000", "AADSTS53001", "AADSTS530003")

_CONSENT_REQUIRED_CODE = 65001
_CONSENT_REQUIRED_SUBSTRING = "AADSTS65001"
_CONSENT_REQUIRED_SUBERROR = "consent_required"


def classify_msal_error(
    result: dict,
    *,
    tenant: str | None = None,
    client_id: str | None = None,
    redirect_uri: str | None = None,
) -> ClassifiedMsalError:
    """Classify a failed MSAL error-result dict.

    ``result`` is the dict MSAL returns on failure from ``acquire_token_silent_with_error`` or
    ``acquire_token_by_auth_code_flow`` -- it typically carries ``"error"``, ``"error_description"``,
    optionally ``"claims"``, ``"suberror"``, and ``"error_codes"`` (a list of int AADSTS codes).

    This function never produces :attr:`MsalErrorCategory.NETWORK_ERROR` -- per the shared contract,
    network/connection exceptions are caught directly by the caller around the MSAL call site and
    turned into ``ClassifiedMsalError(category=MsalErrorCategory.NETWORK_ERROR)`` inline, without
    ever reaching this function.

    ``tenant``, ``client_id``, and ``redirect_uri`` are optional; when classification lands on
    :attr:`MsalErrorCategory.CONSENT_REQUIRED` and all three are supplied, ``admin_consent_url`` is
    composed via :func:`compose_admin_consent_url`. If any is missing, ``admin_consent_url`` is left
    ``None`` -- this function never guesses at tenant/client_id/redirect_uri from the result dict
    itself, since MSAL error-result dicts do not reliably carry them.

    Precedence (checked in this order, first match wins): DEVICE_CA_BLOCKED, CONSENT_REQUIRED,
    CLAIMS_CHALLENGE, INVALID_GRANT, UNKNOWN. A non-empty ``"claims"`` key always means
    CLAIMS_CHALLENGE regardless of the ``"error"`` value (per the shared contract), except when a
    device-state or consent-required signal is also present, in which case those take priority --
    those two categories are terminal/dedicated states which spec.md §6.5/§9 says must never be
    mistaken for an ordinary claims challenge.
    """

    error = result.get("error")
    error_description = result.get("error_description") or ""
    suberror = result.get("suberror")
    error_codes = result.get("error_codes") or []
    claims = result.get("claims")

    raw_error = result.get("error")
    raw_error_description = result.get("error_description")

    if _is_device_ca_blocked(error_codes, error_description):
        return ClassifiedMsalError(
            category=MsalErrorCategory.DEVICE_CA_BLOCKED,
            raw_error=raw_error,
            raw_error_description=raw_error_description,
        )

    if _is_consent_required(suberror, error_codes, error_description):
        admin_consent_url = None
        if tenant is not None and client_id is not None and redirect_uri is not None:
            admin_consent_url = compose_admin_consent_url(tenant, client_id, redirect_uri)
        return ClassifiedMsalError(
            category=MsalErrorCategory.CONSENT_REQUIRED,
            admin_consent_url=admin_consent_url,
            raw_error=raw_error,
            raw_error_description=raw_error_description,
        )

    if claims:
        return ClassifiedMsalError(
            category=MsalErrorCategory.CLAIMS_CHALLENGE,
            claims=claims,
            raw_error=raw_error,
            raw_error_description=raw_error_description,
        )

    if error == "invalid_grant":
        return ClassifiedMsalError(
            category=MsalErrorCategory.INVALID_GRANT,
            raw_error=raw_error,
            raw_error_description=raw_error_description,
        )

    return ClassifiedMsalError(
        category=MsalErrorCategory.UNKNOWN,
        raw_error=raw_error,
        raw_error_description=raw_error_description,
    )


def _is_device_ca_blocked(error_codes: list, error_description: str) -> bool:
    if any(code in _DEVICE_CA_BLOCKED_CODES for code in error_codes):
        return True
    return any(marker in error_description for marker in _DEVICE_CA_BLOCKED_SUBSTRINGS)


def _is_consent_required(suberror: str | None, error_codes: list, error_description: str) -> bool:
    if suberror == _CONSENT_REQUIRED_SUBERROR:
        return True
    if _CONSENT_REQUIRED_CODE in error_codes:
        return True
    return _CONSENT_REQUIRED_SUBSTRING in error_description


def compose_admin_consent_url(tenant: str, client_id: str, redirect_uri: str) -> str:
    """Build the standard Microsoft admin-consent URL (§9).

    ``redirect_uri`` is URL-encoded; ``tenant`` and ``client_id`` are inserted as given (they are
    GUIDs or well-known tenant aliases, not free-form text requiring encoding here).
    """

    encoded_redirect_uri = quote(redirect_uri, safe="")
    return (
        f"https://login.microsoftonline.com/{tenant}/adminconsent"
        f"?client_id={client_id}&redirect_uri={encoded_redirect_uri}"
    )
