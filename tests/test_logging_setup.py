"""Unit tests for winonlinux.logging_setup (tasks.md section 4, spec.md "Log redaction").

Every test exercises the public API only (configure_logging / RdpwContent / mark_rdpw_content /
REDACTION_MARKER) plus pytest's caplog fixture -- none reach into the module's private helpers,
matching how a caller would actually observe redaction.
"""

import logging

import pytest

from winonlinux.logging_setup import (
    REDACTION_MARKER,
    RdpwContent,
    configure_logging,
    mark_rdpw_content,
)

pytestmark = pytest.mark.unit

# A syntactically real-shaped (but fake) JWT: header.payload.signature, each segment base64url.
FAKE_ACCESS_TOKEN = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6ImFiYzEyMyJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)

# An opaque (non-JWT) long token, shaped like a refresh token: a long run of base64url-ish chars.
FAKE_OPAQUE_TOKEN = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0"

FAKE_AUTH_HEADER = f"Authorization: Bearer {FAKE_ACCESS_TOKEN}"

FAKE_UPN = "alice@contoso.onmicrosoft.com"

# A body shaped like a real .rdpw: contains the two keys guaranteed to appear in every AVD
# connection file (spec.md's fixture list) so the pattern-based detector fires.
FAKE_RDPW_BODY = (
    "full address:s:cpc-abc123.avd.wvd.microsoft.com\r\n"
    f"username:s:{FAKE_UPN}\r\n"
    "gatewayhostname:s:gw.wvd.microsoft.com\r\n"
)

# A fragment that does NOT contain "full address:s:" or "username:s:" -- pattern matching alone
# would miss this, which is exactly why the explicit RdpwContent marker exists.
UNMATCHABLE_RDPW_FRAGMENT = "gatewayhostname:s:gw.wvd.microsoft.com"


@pytest.fixture()
def test_logger() -> logging.Logger:
    logger = logging.getLogger("winonlinux.test.logging_setup")
    logger.setLevel(logging.DEBUG)
    return logger


# --- Token redaction: always redacted, both levels -------------------------------------------


def test_token_redacted_at_default_level(caplog, test_logger):
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("access token acquired: %s", FAKE_ACCESS_TOKEN)
    assert FAKE_ACCESS_TOKEN not in caplog.text
    assert REDACTION_MARKER in caplog.text


def test_token_redacted_at_verbose_level(caplog, test_logger):
    configure_logging(verbose=True)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("access token acquired: %s", FAKE_ACCESS_TOKEN)
    assert FAKE_ACCESS_TOKEN not in caplog.text
    assert REDACTION_MARKER in caplog.text


# --- Authorization header redaction: always redacted, both levels ----------------------------


def test_authorization_header_redacted_at_default_level(caplog, test_logger):
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("sending request header %s", FAKE_AUTH_HEADER)
    assert FAKE_ACCESS_TOKEN not in caplog.text
    assert REDACTION_MARKER in caplog.text


def test_authorization_header_redacted_at_verbose_level(caplog, test_logger):
    configure_logging(verbose=True)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("sending request header %s", FAKE_AUTH_HEADER)
    assert FAKE_ACCESS_TOKEN not in caplog.text
    assert REDACTION_MARKER in caplog.text


# --- .rdpw content redaction: always redacted, both levels ------------------------------------


def test_rdpw_content_redacted_at_default_level(caplog, test_logger):
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("composed config:\n%s", FAKE_RDPW_BODY)
    assert "cpc-abc123.avd.wvd.microsoft.com" not in caplog.text
    assert FAKE_UPN not in caplog.text
    assert REDACTION_MARKER in caplog.text


def test_rdpw_content_redacted_at_verbose_level(caplog, test_logger):
    # Even with verbose=True (which would otherwise show UPNs), .rdpw content is still fully
    # redacted -- it is not gated by the verbose flag.
    configure_logging(verbose=True)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("composed config:\n%s", FAKE_RDPW_BODY)
    assert "cpc-abc123.avd.wvd.microsoft.com" not in caplog.text
    assert FAKE_UPN not in caplog.text
    assert REDACTION_MARKER in caplog.text


def test_rdpw_content_redacted_via_explicit_marker(caplog, test_logger):
    # This fragment does not match the "full address:s:"/"username:s:" pattern, so only the
    # explicit RdpwContent marker catches it -- proving the marker path works independently of
    # pattern matching.
    configure_logging(verbose=False)
    marked = mark_rdpw_content(UNMATCHABLE_RDPW_FRAGMENT)
    assert isinstance(marked, RdpwContent)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("fragment=%s", marked)
    assert "gw.wvd.microsoft.com" not in caplog.text
    assert REDACTION_MARKER in caplog.text


# --- UPN redaction: default-level only, visible when verbose ----------------------------------


def test_upn_redacted_at_default_level(caplog, test_logger):
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("signed in as %s", FAKE_UPN)
    assert FAKE_UPN not in caplog.text
    assert REDACTION_MARKER in caplog.text


def test_upn_visible_at_verbose_level(caplog, test_logger):
    configure_logging(verbose=True)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("signed in as %s", FAKE_UPN)
    assert FAKE_UPN in caplog.text


# --- Mechanism coverage: %-style lazy args vs. a plain pre-formatted string --------------------


def test_percent_style_lazy_arg_is_redacted(caplog, test_logger):
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("token=%s", FAKE_OPAQUE_TOKEN)
    assert FAKE_OPAQUE_TOKEN not in caplog.text
    assert REDACTION_MARKER in caplog.text


def test_plain_preformatted_string_is_redacted(caplog, test_logger):
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        # Built with a plain f-string before it ever reaches the logger -- no %-args involved.
        test_logger.info(f"token={FAKE_OPAQUE_TOKEN}")
    assert FAKE_OPAQUE_TOKEN not in caplog.text
    assert REDACTION_MARKER in caplog.text


# --- Mechanism coverage: structured/extra kwargs -----------------------------------------------


def test_extra_kwarg_upn_is_redacted_at_default_level(caplog, test_logger):
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("account event", extra={"upn": FAKE_UPN})
    record = caplog.records[-1]
    assert record.upn == REDACTION_MARKER


def test_extra_kwarg_token_nested_in_a_dict_is_redacted(caplog, test_logger):
    # A secret one level deep inside a structured extra value -- not just a flat string extra --
    # must still be caught; redaction is only useful if callers cannot defeat it by nesting.
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        test_logger.info(
            "token response", extra={"response": {"access_token": FAKE_ACCESS_TOKEN}}
        )
    record = caplog.records[-1]
    assert record.response["access_token"] == REDACTION_MARKER
    assert FAKE_ACCESS_TOKEN not in caplog.text


def test_extra_kwarg_authorization_nested_in_a_list_is_redacted(caplog, test_logger):
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        test_logger.info(
            "outgoing headers", extra={"headers": [f"Authorization: Bearer {FAKE_ACCESS_TOKEN}"]}
        )
    record = caplog.records[-1]
    assert FAKE_ACCESS_TOKEN not in record.headers[0]
    assert REDACTION_MARKER in record.headers[0]


# --- Mechanism coverage: exception tracebacks ---------------------------------------------------


def test_exception_traceback_token_is_redacted(caplog, test_logger):
    # logger.exception()/exc_info renders the traceback text lazily, in the handler, well after
    # this record was constructed -- a real leak path if the factory/filter doesn't handle it.
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        try:
            raise RuntimeError(f"token acquisition failed for token={FAKE_ACCESS_TOKEN}")
        except RuntimeError:
            test_logger.exception("token acquisition failed")
    assert FAKE_ACCESS_TOKEN not in caplog.text
    assert REDACTION_MARKER in caplog.text


def test_exception_traceback_authorization_header_is_redacted(caplog, test_logger):
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        try:
            raise RuntimeError(f"request failed, headers={{'Authorization': 'Bearer {FAKE_ACCESS_TOKEN}'}}")
        except RuntimeError:
            test_logger.exception("graph call failed")
    assert FAKE_ACCESS_TOKEN not in caplog.text
    assert REDACTION_MARKER in caplog.text


# --- Mechanism coverage: dict-shaped Authorization header --------------------------------------


def test_authorization_header_redacted_in_dict_repr_shape(caplog, test_logger):
    # No space/newline between the key's closing quote and the colon -- the shape a Python
    # dict-repr or JSON produces, e.g. str({'Authorization': 'Basic ...'}), as distinct from the
    # plain "Authorization: Bearer ..." string form every other test above uses.
    configure_logging(verbose=False)
    short_basic_credential = "dXNlcjpwYXNzd29yZA"  # not JWT-shaped, not 32+ chars either
    with caplog.at_level(logging.DEBUG):
        test_logger.info(
            "request=%s", {"Authorization": f"Basic {short_basic_credential}"}
        )
    assert short_basic_credential not in caplog.text
    assert REDACTION_MARKER in caplog.text


# --- GUID carve-out (fix-redaction-hardening, audit F-02) -----------------------------------


FAKE_CLOUDPC_GUID = "550e8400-e29b-41d4-a716-446655440000"
FAKE_HOME_ACCOUNT_ID = "550e8400-e29b-41d4-a716-446655440000.11112222-3333-4444-5555-666677778888"


def test_bare_guid_survives_redaction(caplog, test_logger):
    """Cloud PC ids are the log lines' primary correlators -- they must survive verbatim while
    token shapes die (the audit's acceptance bar for F-02)."""
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("cloudpc id %s changed status", FAKE_CLOUDPC_GUID)
    assert FAKE_CLOUDPC_GUID in caplog.text
    assert REDACTION_MARKER not in caplog.text


def test_dotted_uuid_pair_home_account_id_survives_redaction(caplog, test_logger):
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("account %s: silent acquisition ok", FAKE_HOME_ACCOUNT_ID)
    assert FAKE_HOME_ACCOUNT_ID in caplog.text
    assert REDACTION_MARKER not in caplog.text


def test_guid_survives_while_opaque_token_on_same_line_dies(caplog, test_logger):
    configure_logging(verbose=False)
    with caplog.at_level(logging.DEBUG):
        test_logger.info("id %s token %s", FAKE_CLOUDPC_GUID, FAKE_OPAQUE_TOKEN)
    assert FAKE_CLOUDPC_GUID in caplog.text
    token_leaked = FAKE_OPAQUE_TOKEN in caplog.text
    assert not token_leaked
    assert REDACTION_MARKER in caplog.text


def test_undashed_long_run_still_redacted(caplog, test_logger):
    configure_logging(verbose=False)
    undashed = "A" * 40
    with caplog.at_level(logging.DEBUG):
        test_logger.info("suspicious value %s", undashed)
    leaked = undashed in caplog.text
    assert not leaked
    assert REDACTION_MARKER in caplog.text


def test_jwt_embedding_a_uuid_shaped_segment_is_fully_redacted(caplog, test_logger):
    """The carve-out must not create a bypass: a JWT whose payload segment contains a
    UUID-shaped substring is still redacted in full (the JWT pattern wins before the carve-out
    runs)."""
    configure_logging(verbose=False)
    jwt_with_uuid = (
        "eyJhbGciOiJSUzI1NiIsImtpZCI6ImFiYzEyMyJ9."
        f"eyJvaWQiOiI{FAKE_CLOUDPC_GUID.replace('-', '')}xyz1234567890."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    with caplog.at_level(logging.DEBUG):
        test_logger.info("raw result %s", jwt_with_uuid)
    leaked = jwt_with_uuid in caplog.text
    assert not leaked
    assert REDACTION_MARKER in caplog.text


def test_uuid_embedded_inside_a_longer_token_run_is_not_carved_out(caplog, test_logger):
    """A canonical-UUID substring INSIDE a longer base64url run stays part of that run and dies
    with it -- the carve-out's lookarounds require the UUID to stand alone."""
    configure_logging(verbose=False)
    token_with_uuid = f"abcdefgh{FAKE_CLOUDPC_GUID}ijklmnopqrstuvwxyz0123"
    with caplog.at_level(logging.DEBUG):
        test_logger.info("value %s", token_with_uuid)
    leaked = FAKE_CLOUDPC_GUID in caplog.text
    assert not leaked
    assert REDACTION_MARKER in caplog.text
