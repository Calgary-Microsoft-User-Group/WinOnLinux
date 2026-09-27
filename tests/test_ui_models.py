"""Unit tests for winonlinux.ui_models (add-ui-shell change, tasks.md 1.6).

The widget-free half of the UI shell: everything here is what §11.2's manual pass cannot cover
in CI -- grouping, the §4.4 state selection, §7.4 fail-closed enablement, and the FR-2-AC-1
disabled-never-hidden invariant.
"""

from __future__ import annotations

import pytest

from winonlinux import graph_client
from winonlinux.auth_state import AccountAuthState, AuthState
from winonlinux.avd_bookmarks import NATIVE_DISABLED_REASON, AvdBookmark
from winonlinux.cloudpc_provider import (
    CloudPcEntry,
    ConsentRequired,
    Empty,
    Enumerated,
    Failed,
    NoLicence,
)
from winonlinux.ui_models import (
    Availability,
    ConnectMethod,
    PendingTransitions,
    UiState,
    account_rows,
    build_groups,
    cloudpc_actions,
    cloudpc_connect_gate,
    connect_methods,
    reauth_banner_text,
    select_ui_state,
)

pytestmark = pytest.mark.unit


def _entry(entry_id: str = "cpc-1", status: str = "provisioned", name: str = "Standard 4vCPU") -> CloudPcEntry:
    return CloudPcEntry(id=entry_id, display_name=name, status=status)


# --- 1.2 grouping (§4.1) --------------------------------------------------------------------


def test_groups_cloudpcs_then_avd_by_workspace():
    bookmarks = [
        AvdBookmark(workspace_id="ws-fin", resource_id="d1", display_name="Finance Desktop", kind="desktop"),
        AvdBookmark(workspace_id="ws-fin", resource_id="a1", display_name="Excel", kind="remoteapp"),
        AvdBookmark(workspace_id="ws-eng", resource_id="d2", display_name="Eng Desktop", kind="desktop"),
    ]
    groups = build_groups([_entry()], bookmarks)

    assert [(g.provider, g.workspace) for g in groups] == [
        ("Windows 365", None),
        ("Azure Virtual Desktop", "ws-fin"),
        ("Azure Virtual Desktop", "ws-eng"),
    ]
    assert [row.title for row in groups[1].rows] == ["Finance Desktop", "Excel"]
    assert groups[1].rows[1].type_label == "RemoteApp"
    # Cloud PC rows carry the Graph id verbatim as the key (FR-1-AC-2); AVD rows the
    # workspace/resource pair.
    assert groups[0].rows[0].key == "cpc-1"
    assert groups[1].rows[0].key == "ws-fin/d1"


def test_empty_groups_are_omitted_not_rendered_empty():
    assert build_groups([], []) == []
    only_avd = build_groups(
        [], [AvdBookmark(workspace_id="ws", resource_id="r", display_name="D", kind="desktop")]
    )
    assert [g.provider for g in only_avd] == ["Azure Virtual Desktop"]


# --- 1.3 state selection (§4.4, FR-1-AC-3, FR-1-AC-4) ----------------------------------------


def test_loading_before_any_outcome():
    assert select_ui_state(refresh_in_flight=True, latest=None).state is UiState.LOADING
    assert select_ui_state(refresh_in_flight=False, latest=None).state is UiState.LOADING


def test_both_empty_states_are_distinct_and_not_errors():
    empty = select_ui_state(refresh_in_flight=False, latest=Empty())
    no_licence = select_ui_state(refresh_in_flight=False, latest=NoLicence())

    assert empty.state is UiState.EMPTY
    assert no_licence.state is UiState.NO_LICENCE
    assert empty.state is not no_licence.state  # FR-1-AC-3: DISTINCT
    for selection in (empty, no_licence):
        assert not selection.offer_retry  # neither renders as an error
        assert not selection.greyed


def test_failed_refresh_keeps_previous_list_with_error_and_retry():
    previous = [_entry()]
    failed = Failed(error=graph_client.GraphError("boom"), previous_entries=previous)

    selection = select_ui_state(refresh_in_flight=False, latest=failed)

    assert selection.state is UiState.ERROR
    assert selection.entries == previous  # FR-1-AC-4: never cleared
    assert selection.offer_retry
    assert selection.banner_message == failed.user_message


def test_network_failure_selects_offline_greyed_with_connects_disabled_semantics():
    failed = Failed(error=graph_client.GraphNetworkError("unreachable"), previous_entries=[_entry()])
    selection = select_ui_state(refresh_in_flight=False, latest=failed)

    assert selection.state is UiState.OFFLINE
    assert selection.greyed
    assert selection.entries  # cached list still shown, greyed (§4.4)


def test_proxy_failure_is_error_never_plain_offline():
    """§9: a proxy failure is named as such, never reported as plain offline."""
    failed = Failed(error=graph_client.GraphProxyError("proxy said no"), previous_entries=[])
    selection = select_ui_state(refresh_in_flight=False, latest=failed)

    assert selection.state is UiState.ERROR
    assert "proxy" in selection.banner_message.lower()


def test_consent_required_selects_the_guided_surface():
    selection = select_ui_state(
        refresh_in_flight=False, latest=ConsentRequired(admin_consent_url=None)
    )
    assert selection.state is UiState.CONSENT_REQUIRED
    assert "administrator" in selection.banner_message


def test_success_selects_normal_with_entries():
    entries = [_entry(), _entry("cpc-2")]
    selection = select_ui_state(refresh_in_flight=False, latest=Enumerated(entries=entries))
    assert selection.state is UiState.NORMAL
    assert selection.entries == entries


# --- 1.4 §7.4 enablement: fail closed, disabled-never-hidden, pending suppression -------------


def test_unknown_status_fails_closed_on_both_methods_with_reason():
    for status in ("restoring", "someFutureStatus", "", None):
        methods = connect_methods(cloudpc_connect_gate(status))
        assert set(methods) == {ConnectMethod.NATIVE, ConnectMethod.WEB}  # never hidden
        for availability in methods.values():
            assert not availability.enabled
            assert availability.reason  # the stated reason is visible (FR-2-AC-1)


def test_connectable_status_defers_to_launcher_availability():
    methods = connect_methods(cloudpc_connect_gate("provisioned"))
    # Status permits connection, but this build ships no launchers -- both disabled with the
    # build reason, not the status reason.
    assert not methods[ConnectMethod.WEB].enabled
    assert "build" in methods[ConnectMethod.WEB].reason
    assert not methods[ConnectMethod.NATIVE].enabled


def test_avd_native_override_carries_the_phase0_reason():
    groups = build_groups(
        [], [AvdBookmark(workspace_id="ws", resource_id="r", display_name="D", kind="desktop")]
    )
    methods = groups[0].rows[0].methods
    assert methods[ConnectMethod.NATIVE].reason == NATIVE_DISABLED_REASON
    assert ConnectMethod.WEB in methods  # both always present


def test_disabled_availability_without_reason_is_a_construction_error():
    with pytest.raises(ValueError):
        Availability(False)


def test_pending_transition_suppresses_actions_until_refresh_clears():
    pending = PendingTransitions()
    pending.mark("cpc-1", "restart")

    suppressed = cloudpc_actions("cpc-1", pending=pending)
    assert all(not availability.enabled for availability in suppressed.values())
    assert any("restart" in a.reason for a in suppressed.values())

    other = cloudpc_actions("cpc-2", pending=pending)
    assert all("restart" not in (a.reason or "") for a in other.values())  # scoped per resource

    pending.clear_on_refresh()
    cleared = cloudpc_actions("cpc-1", pending=pending)
    # Still disabled in this build (actions service unapplied) but no longer for the pending
    # reason -- the suppression itself lifted with the refresh.
    assert all("restart" not in (a.reason or "") for a in cleared.values())


def test_admin_actions_absent_unless_capability_affirmed():
    plain = cloudpc_actions("cpc-1")  # capability unknown -> not capable (FR-5-AC-3)
    assert set(plain) == {"restart", "rename", "troubleshoot", "reprovision"}
    assert "restore" not in plain and "resize" not in plain

    admin = cloudpc_actions("cpc-1", admin_capable=True)
    assert {"restore", "resize"} <= set(admin)


# --- account rows and banner wording (§4.1, §4.4) ---------------------------------------------


def test_account_rows_carry_active_flag_and_reauth_badge():
    healthy = AccountAuthState("acct-a", login_hint="user@contoso.com")
    healthy.mark_active()
    needs_reauth = AccountAuthState("acct-b", login_hint="admin@fabrikam.com")
    needs_reauth.mark_active()
    from winonlinux.auth_errors import ClassifiedMsalError, MsalErrorCategory

    needs_reauth.apply_error(ClassifiedMsalError(category=MsalErrorCategory.INVALID_GRANT))

    rows = account_rows({"acct-a": healthy, "acct-b": needs_reauth}, "acct-a")

    by_id = {row.home_account_id: row for row in rows}
    assert by_id["acct-a"].active and by_id["acct-a"].badge is None
    assert not by_id["acct-b"].active
    assert by_id["acct-b"].badge == "reauthentication required"
    assert by_id["acct-b"].label == "admin@fabrikam.com"


def test_reauth_banner_wording_matches_spec():
    text = reauth_banner_text("user@contoso.com", "acct-a")
    assert text == "Your sign-in for user@contoso.com has expired. Sign in again."
    assert reauth_banner_text(None, "acct-a") == "Your sign-in for acct-a has expired. Sign in again."
