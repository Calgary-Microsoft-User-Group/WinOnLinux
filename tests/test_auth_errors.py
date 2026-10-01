"""Unit tests for winonlinux.auth_errors (tasks.md 3.2, 7.5).

Pure dict-in/dataclass-out tests: no asyncio, no external dependencies beyond the module itself.
"""

import pytest

from winonlinux.auth_errors import (
    ClassifiedMsalError,
    MsalErrorCategory,
    classify_msal_error,
    compose_admin_consent_url,
)

pytestmark = pytest.mark.unit


def test_invalid_grant_is_classified() -> None:
    result = {
        "error": "invalid_grant",
        "error_description": "AADSTS70008: The refresh token has expired due to inactivity.",
        "error_codes": [70008],
    }

    classified = classify_msal_error(result)

    assert classified.category is MsalErrorCategory.INVALID_GRANT
    assert classified.claims is None
    assert classified.admin_consent_url is None


def test_claims_challenge_is_classified_with_verbatim_claims() -> None:
    claims_value = '{"access_token":{"acrs":{"essential":true,"value":"c1"}}}'
    result = {
        "error": "interaction_required",
        "error_description": "AADSTS53003: Access has been blocked by Conditional Access policies.",
        "claims": claims_value,
    }

    classified = classify_msal_error(result)

    assert classified.category is MsalErrorCategory.CLAIMS_CHALLENGE
    assert classified.claims == claims_value


def test_claims_challenge_takes_priority_over_unrelated_error_value() -> None:
    """A non-empty 'claims' key wins regardless of the 'error' value (shared contract)."""

    claims_value = '{"access_token":{"acrs":{"essential":true,"value":"c1"}}}'
    result = {
        "error": "invalid_grant",
        "error_description": "some unrelated description",
        "claims": claims_value,
    }

    classified = classify_msal_error(result)

    assert classified.category is MsalErrorCategory.CLAIMS_CHALLENGE
    assert classified.claims == claims_value


def test_device_ca_blocked_via_error_codes() -> None:
    result = {
        "error": "invalid_grant",
        "error_description": "AADSTS53000: Device is not in required device state: compliant.",
        "error_codes": [53000],
    }

    classified = classify_msal_error(result)

    assert classified.category is MsalErrorCategory.DEVICE_CA_BLOCKED


def test_device_ca_blocked_via_error_description_substring_only() -> None:
    """error_codes may be absent/empty; a matching AADSTS substring in the description is enough."""

    result = {
        "error": "invalid_grant",
        "error_description": "AADSTS530003: Your device is required to be marked as compliant.",
        "error_codes": [],
    }

    classified = classify_msal_error(result)

    assert classified.category is MsalErrorCategory.DEVICE_CA_BLOCKED


def test_device_ca_blocked_takes_priority_over_claims() -> None:
    result = {
        "error": "interaction_required",
        "error_description": "AADSTS53001: Device is not domain joined.",
        "error_codes": [53001],
        "claims": '{"access_token":{}}',
    }

    classified = classify_msal_error(result)

    assert classified.category is MsalErrorCategory.DEVICE_CA_BLOCKED


def test_consent_required_via_suberror() -> None:
    result = {
        "error": "invalid_grant",
        "suberror": "consent_required",
        "error_description": "AADSTS65001: The user or administrator has not consented.",
    }

    classified = classify_msal_error(result)

    assert classified.category is MsalErrorCategory.CONSENT_REQUIRED


def test_consent_required_via_error_codes() -> None:
    result = {
        "error": "invalid_grant",
        "error_codes": [65001],
        "error_description": "consent is required",
    }

    classified = classify_msal_error(result)

    assert classified.category is MsalErrorCategory.CONSENT_REQUIRED


def test_consent_required_composes_admin_consent_url_when_context_supplied() -> None:
    result = {
        "error": "invalid_grant",
        "suberror": "consent_required",
        "error_description": "AADSTS65001: The user or administrator has not consented.",
    }

    classified = classify_msal_error(
        result,
        tenant="contoso.onmicrosoft.com",
        client_id="00000000-0000-0000-0000-000000000000",
        redirect_uri="http://127.0.0.1:54321/callback",
    )

    assert classified.category is MsalErrorCategory.CONSENT_REQUIRED
    assert classified.admin_consent_url == (
        "https://login.microsoftonline.com/contoso.onmicrosoft.com/adminconsent"
        "?client_id=00000000-0000-0000-0000-000000000000"
        "&redirect_uri=http%3A%2F%2F127.0.0.1%3A54321%2Fcallback"
    )


def test_consent_required_leaves_admin_consent_url_none_without_context() -> None:
    result = {
        "error": "invalid_grant",
        "suberror": "consent_required",
        "error_description": "AADSTS65001: The user or administrator has not consented.",
    }

    classified = classify_msal_error(result)

    assert classified.category is MsalErrorCategory.CONSENT_REQUIRED
    assert classified.admin_consent_url is None


def test_network_error_is_never_produced_by_the_classifier() -> None:
    """NETWORK_ERROR is only ever constructed directly by callers around a caught exception."""

    result = {"error": "invalid_grant", "error_description": "some network-ish text"}

    classified = classify_msal_error(result)

    assert classified.category is not MsalErrorCategory.NETWORK_ERROR


def test_unmatched_dict_classifies_as_unknown() -> None:
    result = {"error": "invalid_scope", "error_description": "AADSTS70011: unsupported scope."}

    classified = classify_msal_error(result)

    assert classified.category is MsalErrorCategory.UNKNOWN


def test_empty_dict_classifies_as_unknown() -> None:
    classified = classify_msal_error({})

    assert classified.category is MsalErrorCategory.UNKNOWN
    assert classified.raw_error is None
    assert classified.raw_error_description is None


def test_raw_error_and_description_are_preserved_through_classification() -> None:
    result = {
        "error": "invalid_grant",
        "error_description": "AADSTS70008: The refresh token has expired due to inactivity.",
    }

    classified = classify_msal_error(result)

    assert classified.raw_error == "invalid_grant"
    assert classified.raw_error_description == (
        "AADSTS70008: The refresh token has expired due to inactivity."
    )


def test_classified_msal_error_is_frozen_dataclass() -> None:
    classified = ClassifiedMsalError(category=MsalErrorCategory.UNKNOWN)

    with pytest.raises(Exception):
        classified.category = MsalErrorCategory.INVALID_GRANT  # type: ignore[misc]


def test_compose_admin_consent_url_encodes_special_characters() -> None:
    url = compose_admin_consent_url(
        tenant="contoso.onmicrosoft.com",
        client_id="11111111-2222-3333-4444-555555555555",
        redirect_uri="http://127.0.0.1:54321/callback?x=1&y=2",
    )

    assert url == (
        "https://login.microsoftonline.com/contoso.onmicrosoft.com/adminconsent"
        "?client_id=11111111-2222-3333-4444-555555555555"
        "&redirect_uri=http%3A%2F%2F127.0.0.1%3A54321%2Fcallback%3Fx%3D1%26y%3D2"
    )
