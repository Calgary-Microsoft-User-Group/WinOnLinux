"""Web launcher: direct-launch URL composition and system-browser handoff (add-web-launcher
change; spec.md §5.3, FR-2).

The product's only working connect path until Stage 3, and deliberately boring: URL composition
is pure functions (unit-tested with no network, §13.2/§13.4), the Graph beta launch-detail call
is an optional fast path that can never sit on the critical path, and the browser is the
system's (the OpenURI portal chooses it under Flatpak, §5.8/§10.5).

Composition rules (§5.3, FR-2-AC-3): query (``?tenant=``) before fragment; ``#loginHint=<active
account UPN>`` always appended, always the LAST component; the UPN appears in the fragment ONLY
-- never the query or path -- so it is not transmitted in the HTTP request (§10.5's documented
trade-off: it does enter browser history).

``?tenant=`` interpretation: §5.3 says to include it "for accounts whose home tenant differs
from the browser's default" -- a condition this application cannot observe (the browser's
signed-in state is invisible to it). The rule implemented is: include ``?tenant=`` whenever the
tenant is known. That satisfies the differs case exactly where it matters (multi-account,
wrong-default-tenant) and is harmless when the default already matches. Recorded here rather
than silently narrowed.

Verified vs assumed (design.md): the ``ent/``/``avd/`` URL shapes and composition rules are
documented (July 2026) but are a product feature, not a versioned API (§12 risk 7) -- hence one
composition module, so format churn is a one-file fix. The tenant-id-from-``home_account_id``
derivation rides on MSAL's ``oid.tid`` convention (ASSUMED, flagged in auth_manager). Whether
components may be appended to the Microsoft-issued ``cloudPcLaunchUrl`` is an open question;
the conservative rule (append ``#loginHint`` only when the issued URL has no fragment; never
touch its path/query) is implemented pending the live-tenant finding (tasks.md 5.2).

Security note: the issued ``cloudPcLaunchUrl`` is REMOTE-SUPPLIED data handed to a browser.
The same principle that guards Graph paging URLs (§10.1, audit F-01) applies before handoff:
scheme must be ``https`` and the host must sit under a Microsoft-owned suffix, else the issued
URL is discarded (logged) and the constructed URL is used. ``getCloudPcLaunchInfo`` -- hard
stop 2026-10-30 -- is never referenced.
"""

from __future__ import annotations

import logging
import webbrowser
from urllib.parse import quote, urlsplit

from winonlinux import graph_client
from winonlinux.avd_bookmarks import AvdBookmark
from winonlinux.task_registry import run_blocking

__all__ = [
    "BrowserSpawnFailed",
    "WebLauncher",
    "build_cloudpc_web_url",
    "build_avd_web_url",
    "tenant_id_from_home_account_id",
]

logger = logging.getLogger(__name__)

_WEBCLIENT_BASE = "https://windows.cloud.microsoft/webclient"

_LAUNCH_DETAIL_URL_TEMPLATE = (
    "https://graph.microsoft.com/beta/me/cloudPCs/{cloudpc_id}/retrieveCloudPcLaunchDetail"
)

#: Host suffixes an issued ``cloudPcLaunchUrl`` must sit under to be opened (label-boundary
#: matched). §5.3 documents the rdweb-*.wvdselfhost.microsoft.com form; the webclient host
#: lives under the ``cloud.microsoft`` TLD.
_ISSUED_URL_HOST_SUFFIXES = (".microsoft.com", ".cloud.microsoft")


class BrowserSpawnFailed(Exception):
    """The system browser could not be spawned (§9: error toast offering the URL for manual
    copy -- NOT a resource or account error). ``url`` is what the UI offers to copy."""

    user_message = (
        "The browser could not be opened. Copy the link and open it in a browser yourself."
    )

    def __init__(self, url: str) -> None:
        super().__init__(f"system browser failed to spawn for a web launch")
        self.url = url


def tenant_id_from_home_account_id(home_account_id: str) -> str | None:
    """The ``tid`` half of MSAL's ``oid.tid`` convention (ASSUMED -- see auth_manager's module
    docstring); ``None`` when the id does not match that shape."""
    parts = home_account_id.split(".")
    if len(parts) == 2 and all(parts):
        return parts[1]
    return None


def _append_components(base: str, *, tenant_id: str | None, login_hint: str | None) -> str:
    """Apply the §5.3 composition rules to a constructed base URL: query before fragment,
    ``#loginHint=`` always last, UPN in the fragment only."""
    url = base
    if tenant_id:
        url += f"?tenant={quote(tenant_id, safe='')}"
    if login_hint:
        url += f"#loginHint={quote(login_hint, safe='@.')}"
    return url


def build_cloudpc_web_url(
    cloudpc_id: str, *, tenant_id: str | None, login_hint: str | None
) -> str:
    """``https://windows.cloud.microsoft/webclient/ent/<CloudPc.Id>`` from exactly the Graph
    ``id`` retained at enumeration -- no second lookup (FR-1-AC-2, FR-2-AC-3)."""
    return _append_components(
        f"{_WEBCLIENT_BASE}/ent/{quote(cloudpc_id, safe='')}",
        tenant_id=tenant_id,
        login_hint=login_hint,
    )


def build_avd_web_url(
    workspace_id: str, resource_id: str, *, tenant_id: str | None, login_hint: str | None
) -> str:
    """``https://windows.cloud.microsoft/webclient/avd/<workspaceID>/<resourceID>`` from
    admin-provisioned IDs (Phase 0, §11.1), same composition rules."""
    return _append_components(
        f"{_WEBCLIENT_BASE}/avd/{quote(workspace_id, safe='')}/{quote(resource_id, safe='')}",
        tenant_id=tenant_id,
        login_hint=login_hint,
    )


def _issued_url_is_safe(url: object) -> bool:
    """Validate a remote-supplied ``cloudPcLaunchUrl`` before handing it to a browser: string,
    ``https``, host under a Microsoft-owned suffix (label-boundary match)."""
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(
        host.endswith(suffix) for suffix in _ISSUED_URL_HOST_SUFFIXES
    )


class WebLauncher:
    """Composes launch URLs, optionally upgrades them via the beta launch-detail call, and
    hands them to the system browser. One instance per application (holds the per-session
    RemoteApp launch tracking, §12 risk 9)."""

    def __init__(
        self,
        *,
        auth_manager,
        beta_launch_detail_enabled: bool = False,
        scopes: tuple[str, ...] = ("CloudPC.Read.All",),
    ) -> None:
        self._auth = auth_manager
        # §7.2 posture: Graph beta is "production use not supported" -- the launch-detail fast
        # path defaults OFF and web launch is fully functional without it.
        self._beta_enabled = beta_launch_detail_enabled
        self._scopes = tuple(scopes)
        # Workspaces from which a RemoteApp web tab was opened THIS session. §12 risk 9 keys
        # the disconnect behavior on the host pool; Phase 0 bookmarks carry no host-pool id,
        # so the workspace is the closest available grouping (conservative: warns at least as
        # often as the true rule). No cross-session persistence -- a stale warning is worse
        # than none (design.md).
        self._remoteapp_workspaces: set[str] = set()

    # -- URL resolution ------------------------------------------------------------------------

    def _account_components(self, home_account_id: str) -> tuple[str | None, str | None]:
        state = self._auth.accounts.get(home_account_id)
        login_hint = getattr(state, "login_hint", None)
        return tenant_id_from_home_account_id(home_account_id), login_hint

    async def resolve_cloudpc_url(self, cloudpc_id: str, home_account_id: str) -> str:
        """The URL a Cloud PC launch opens: the Microsoft-issued ``cloudPcLaunchUrl`` when the
        beta fast path is on and yields a safe URL, else the constructed ``ent/`` URL. Every
        failure mode of the beta path degrades silently to construction (§7.2) -- including
        auth failures, since opening a URL needs no token."""
        tenant_id, login_hint = self._account_components(home_account_id)
        constructed = build_cloudpc_web_url(
            cloudpc_id, tenant_id=tenant_id, login_hint=login_hint
        )
        if not self._beta_enabled:
            return constructed

        try:
            detail = await graph_client.graph_get_json(
                _LAUNCH_DETAIL_URL_TEMPLATE.format(cloudpc_id=quote(cloudpc_id, safe="")),
                auth_manager=self._auth,
                home_account_id=home_account_id,
                scopes=list(self._scopes),
            )
        except Exception as exc:  # noqa: BLE001 - beta is never on the critical path (§7.2)
            logger.info(
                "web_launcher: retrieveCloudPcLaunchDetail failed (%s); using the constructed "
                "URL (beta degradation, logged for triage)",
                type(exc).__name__,
            )
            return constructed

        issued = detail.get("cloudPcLaunchUrl") if isinstance(detail, dict) else None
        if not _issued_url_is_safe(issued):
            logger.warning(
                "web_launcher: issued cloudPcLaunchUrl missing or outside the allowed hosts; "
                "using the constructed URL (§10.1 allowlist principle)"
            )
            return constructed

        # Conservative composition rule for issued URLs (design.md Open Question, tasks 5.2):
        # never touch path or query; append #loginHint only when no fragment is present.
        if login_hint and "#" not in issued:
            return issued + f"#loginHint={quote(login_hint, safe='@.')}"
        return issued

    def resolve_avd_url(self, bookmark: AvdBookmark, home_account_id: str) -> str:
        tenant_id, login_hint = self._account_components(home_account_id)
        return build_avd_web_url(
            bookmark.workspace_id,
            bookmark.resource_id,
            tenant_id=tenant_id,
            login_hint=login_hint,
        )

    # -- RemoteApp second-tab warning (§12 risk 9) ----------------------------------------------

    def needs_second_remoteapp_warning(self, bookmark: AvdBookmark) -> bool:
        """True when launching this RemoteApp would disconnect one already launched this
        session from the same workspace. The caller warns and proceeds only on confirmation."""
        return bookmark.kind == "remoteapp" and bookmark.workspace_id in self._remoteapp_workspaces

    def note_remoteapp_launch(self, bookmark: AvdBookmark) -> None:
        if bookmark.kind == "remoteapp":
            self._remoteapp_workspaces.add(bookmark.workspace_id)

    # -- browser handoff -------------------------------------------------------------------------

    async def open_in_browser(self, url: str) -> None:
        """Open ``url`` in the system browser (xdg-open -> OpenURI portal under Flatpak, §5.8).

        Raises :class:`BrowserSpawnFailed` -- carrying the URL for the §9 manual-copy toast --
        when the spawn fails. The URL (which carries the UPN in its fragment) is deliberately
        NOT logged at any level (§10.5/§10.7); only the outcome is.
        """
        try:
            opened = await run_blocking(webbrowser.open, url)
        except Exception as exc:  # noqa: BLE001 - classified below, never propagates raw
            logger.warning("web_launcher: browser spawn raised %s", type(exc).__name__)
            raise BrowserSpawnFailed(url) from exc
        if not opened:
            logger.warning("web_launcher: no browser could be spawned for a web launch")
            raise BrowserSpawnFailed(url)
        logger.info("web_launcher: opened a web launch in the system browser")
