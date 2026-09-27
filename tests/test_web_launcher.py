"""Unit tests for winonlinux.web_launcher (add-web-launcher change; §5.3, FR-2-AC-1…AC-3).

URL composition is pure-function tested (§11.2 U-level); the launch-detail fast path is
fixture-tested against valid/mutated/error shapes (§13.3); browser handoff is tested with a
monkeypatched ``webbrowser``. No network, no real browser (§13.4).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from winonlinux import web_launcher
from winonlinux.avd_bookmarks import AvdBookmark
from winonlinux.web_launcher import (
    BrowserSpawnFailed,
    WebLauncher,
    build_avd_web_url,
    build_cloudpc_web_url,
    tenant_id_from_home_account_id,
)

pytestmark = pytest.mark.unit

UPN = "user@contoso.com"
TENANT = "11112222-3333-4444-5555-666677778888"
HOME_ACCOUNT_ID = f"aaaabbbb-cccc-dddd-eeee-ffff00001111.{TENANT}"


def _launcher(*, beta: bool = False, login_hint: str | None = UPN) -> WebLauncher:
    auth = SimpleNamespace(accounts={HOME_ACCOUNT_ID: SimpleNamespace(login_hint=login_hint)})
    return WebLauncher(auth_manager=auth, beta_launch_detail_enabled=beta)


# --- 1.x URL composition (FR-2-AC-3: ordering, encoding, forms) --------------------------------


def test_cloudpc_url_matches_spec_scenario_exactly():
    """The web-launch spec's literal scenario: query before fragment, #loginHint= last."""
    url = build_cloudpc_web_url("X", tenant_id="T", login_hint=UPN)
    assert url == f"https://windows.cloud.microsoft/webclient/ent/X?tenant=T#loginHint={UPN}"


def test_login_hint_is_always_last_and_query_precedes_fragment():
    url = build_cloudpc_web_url("cpc-1", tenant_id=TENANT, login_hint=UPN)
    assert url.endswith(f"#loginHint={UPN}")
    assert url.index("?tenant=") < url.index("#loginHint=")


def test_tenant_omitted_when_unknown_login_hint_still_appended():
    url = build_cloudpc_web_url("cpc-1", tenant_id=None, login_hint=UPN)
    assert "?tenant=" not in url
    assert url.endswith(f"#loginHint={UPN}")


def test_avd_url_form():
    url = build_avd_web_url("W", "R", tenant_id="T", login_hint=UPN)
    assert url == f"https://windows.cloud.microsoft/webclient/avd/W/R?tenant=T#loginHint={UPN}"


def test_upn_appears_only_in_the_fragment():
    """§10.5: the UPN is never in the path or query -- only after #loginHint=."""
    url = build_cloudpc_web_url("cpc-1", tenant_id=TENANT, login_hint=UPN)
    before_fragment, _, fragment = url.partition("#")
    assert UPN not in before_fragment
    assert fragment == f"loginHint={UPN}"


def test_path_components_are_percent_encoded():
    url = build_cloudpc_web_url("weird id/../x?y", tenant_id=None, login_hint=None)
    assert "/ent/weird%20id%2F..%2Fx%3Fy" in url


def test_tenant_id_derivation_from_home_account_id():
    assert tenant_id_from_home_account_id(HOME_ACCOUNT_ID) == TENANT
    assert tenant_id_from_home_account_id("not-a-pair") is None
    assert tenant_id_from_home_account_id("a.b.c") is None


# --- 2.x launch-detail fast path (§7.2 beta posture) --------------------------------------------


def _patch_launch_detail(monkeypatch, response):
    calls: list[str] = []

    async def fake_graph_get_json(url, **kwargs):
        calls.append(url)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(web_launcher.graph_client, "graph_get_json", fake_graph_get_json)
    return calls


ISSUED = (
    "https://rdweb-r0.wvdselfhost.microsoft.com/api/arm/weblaunch/tenants/T/resources/R"
)


def test_flag_off_never_calls_graph_and_uses_constructed_url(monkeypatch):
    calls = _patch_launch_detail(monkeypatch, {"cloudPcLaunchUrl": ISSUED})
    launcher = _launcher(beta=False)

    url = asyncio.run(launcher.resolve_cloudpc_url("cpc-1", HOME_ACCOUNT_ID))

    assert calls == []
    assert url.startswith("https://windows.cloud.microsoft/webclient/ent/cpc-1")
    assert "retrieveCloudPcLaunchDetail" not in url


def test_issued_url_preferred_with_login_hint_appended_when_no_fragment(monkeypatch):
    calls = _patch_launch_detail(monkeypatch, {"cloudPcLaunchUrl": ISSUED})
    launcher = _launcher(beta=True)

    url = asyncio.run(launcher.resolve_cloudpc_url("cpc-1", HOME_ACCOUNT_ID))

    assert len(calls) == 1
    assert "beta/me/cloudPCs/cpc-1/retrieveCloudPcLaunchDetail" in calls[0]
    assert "getCloudPcLaunchInfo" not in calls[0]  # deprecated endpoint never referenced
    assert url == ISSUED + f"#loginHint={UPN}"


def test_issued_url_with_existing_fragment_is_opened_verbatim(monkeypatch):
    """Conservative rule pending the design Open Question: never modify an issued URL's
    components; only append #loginHint when NO fragment is present."""
    issued = ISSUED + "#already-there"
    _patch_launch_detail(monkeypatch, {"cloudPcLaunchUrl": issued})
    launcher = _launcher(beta=True)

    url = asyncio.run(launcher.resolve_cloudpc_url("cpc-1", HOME_ACCOUNT_ID))
    assert url == issued


@pytest.mark.parametrize(
    "response",
    [
        {"unexpected": "shape"},
        {"cloudPcLaunchUrl": 12345},
        {"cloudPcLaunchUrl": "http://rdweb-r0.wvdselfhost.microsoft.com/x"},  # not https
        {"cloudPcLaunchUrl": "https://attacker.example/weblaunch"},  # host outside allowlist
        {"cloudPcLaunchUrl": "https://evilmicrosoft.com/x"},  # suffix must be label-bounded
    ],
)
def test_unsafe_or_mutated_launch_detail_falls_back_to_constructed(monkeypatch, response):
    _patch_launch_detail(monkeypatch, response)
    launcher = _launcher(beta=True)

    url = asyncio.run(launcher.resolve_cloudpc_url("cpc-1", HOME_ACCOUNT_ID))
    assert url.startswith("https://windows.cloud.microsoft/webclient/ent/cpc-1")


def test_launch_detail_error_falls_back_silently(monkeypatch):
    from winonlinux.graph_client import GraphError

    _patch_launch_detail(monkeypatch, GraphError("beta contract change"))
    launcher = _launcher(beta=True)

    url = asyncio.run(launcher.resolve_cloudpc_url("cpc-1", HOME_ACCOUNT_ID))
    assert url.startswith("https://windows.cloud.microsoft/webclient/ent/cpc-1")
    assert url.endswith(f"#loginHint={UPN}")


# --- 4.2 RemoteApp second-tab warning (§12 risk 9) -----------------------------------------------


def test_second_remoteapp_from_same_workspace_warns_and_desktops_never_do():
    launcher = _launcher()
    excel = AvdBookmark(workspace_id="ws-fin", resource_id="a1", display_name="Excel", kind="remoteapp")
    word = AvdBookmark(workspace_id="ws-fin", resource_id="a2", display_name="Word", kind="remoteapp")
    other = AvdBookmark(workspace_id="ws-eng", resource_id="a3", display_name="IDE", kind="remoteapp")
    desktop = AvdBookmark(workspace_id="ws-fin", resource_id="d1", display_name="Desk", kind="desktop")

    assert not launcher.needs_second_remoteapp_warning(excel)  # first launch: no warning
    launcher.note_remoteapp_launch(excel)

    assert launcher.needs_second_remoteapp_warning(word)  # same workspace: warn
    assert not launcher.needs_second_remoteapp_warning(other)  # different workspace: no warn
    assert not launcher.needs_second_remoteapp_warning(desktop)  # desktops never warn

    launcher.note_remoteapp_launch(desktop)  # desktops are never tracked either
    assert not launcher.needs_second_remoteapp_warning(desktop)


# --- 3.x browser handoff -------------------------------------------------------------------------


def test_open_in_browser_success(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(web_launcher.webbrowser, "open", lambda url: opened.append(url) or True)
    launcher = _launcher()

    asyncio.run(launcher.open_in_browser("https://windows.cloud.microsoft/webclient/ent/x"))
    assert opened == ["https://windows.cloud.microsoft/webclient/ent/x"]


def test_spawn_failure_raises_typed_error_carrying_the_url(monkeypatch):
    """§9: browser fails to spawn -> the typed failure carries the URL for the manual-copy
    toast, and its user_message has no raw detail."""
    monkeypatch.setattr(web_launcher.webbrowser, "open", lambda url: False)
    launcher = _launcher()

    with pytest.raises(BrowserSpawnFailed) as excinfo:
        asyncio.run(launcher.open_in_browser("https://example.invalid/launch"))

    assert excinfo.value.url == "https://example.invalid/launch"
    assert "Copy the link" in excinfo.value.user_message


def test_spawn_exception_is_classified_not_propagated_raw(monkeypatch):
    def exploding_open(url):
        raise OSError("no display")

    monkeypatch.setattr(web_launcher.webbrowser, "open", exploding_open)
    launcher = _launcher()

    with pytest.raises(BrowserSpawnFailed):
        asyncio.run(launcher.open_in_browser("https://windows.cloud.microsoft/x"))
