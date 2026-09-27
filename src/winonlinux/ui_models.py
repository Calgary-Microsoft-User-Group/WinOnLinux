"""Widget-free view models for the UI shell (add-ui-shell change, tasks.md section 1).

Everything the widgets render is computed here first, in plain Python: grouping (§4.1), the §4.4
state selection with its error-keeps-list semantics, the §7.4 enablement mechanism, per-account
badges/banners, and the §9 message discipline (only ``user_message`` fields reach these models;
raw error detail never does). design.md: the mappings behind the acceptance criteria are
unit-testable only if kept out of widget code -- the widget layer (``winonlinux.ui_shell``)
renders whatever these models select and contains no decisions of its own.

Two invariants are made structurally unrepresentable rather than merely followed:

- **Disabled-never-hidden (FR-2-AC-1):** :func:`connect_methods` always returns BOTH methods;
  there is no way to omit one. Unavailability is an :class:`Availability` with ``enabled=False``
  and a mandatory reason.
- **Unknown-fails-closed (§7.4):** the status → connectable mapping is a data table
  (:data:`CONNECTABLE_CLOUDPC_STATUSES`); anything absent from it -- including every status this
  build has never heard of -- renders not connectable. The full status × action matrix is
  outstanding (G-19, P1); it drops in as data here, not as new mechanism.

Launcher/actions reality of this build: ``add-web-launcher``, ``add-native-launcher``, and
``add-cloudpc-actions`` are unapplied, so :data:`BUILD_LAUNCHERS` and
:data:`ACTIONS_SERVICE_AVAILABILITY` describe both dispatch paths as unavailable-with-reason.
Those changes flip these inputs when they land; nothing else in this module changes.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field

from winonlinux import graph_client
from winonlinux.auth_state import AuthState
from winonlinux.avd_bookmarks import NATIVE_DISABLED_REASON, AvdBookmark, group_by_workspace
from winonlinux.cloudpc_provider import (
    CloudPcEntry,
    ConsentRequired,
    Empty,
    Enumerated,
    EnumerationResult,
    Failed,
    NoLicence,
)

__all__ = [
    "Availability",
    "ConnectMethod",
    "LauncherAvailability",
    "BUILD_LAUNCHERS",
    "ACTIONS_SERVICE_AVAILABILITY",
    "CONNECTABLE_CLOUDPC_STATUSES",
    "UiState",
    "StateSelection",
    "AccountRow",
    "ResourceRow",
    "ResourceGroup",
    "PendingTransitions",
    "account_rows",
    "reauth_banner_text",
    "select_ui_state",
    "cloudpc_connect_gate",
    "connect_methods",
    "cloudpc_actions",
    "build_groups",
    "cloudpc_resource_key",
    "avd_resource_key",
]

logger = logging.getLogger(__name__)


# --- availability primitives ---------------------------------------------------------------


class ConnectMethod(enum.Enum):
    """The two connection methods every resource entry exposes (§4.2)."""

    NATIVE = "native"
    WEB = "web"


@dataclass(frozen=True)
class Availability:
    """Whether one method/action is offered enabled, and if not, WHY -- the reason is the
    user-visible string FR-2-AC-1 requires, so a disabled availability without one is a
    construction error, not a rendering gap."""

    enabled: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.enabled and not self.reason:
            raise ValueError("a disabled Availability must carry a user-visible reason")


@dataclass(frozen=True)
class LauncherAvailability:
    """What this build can actually dispatch, per method -- the input that add-web-launcher /
    add-native-launcher flip when they land."""

    native: Availability
    web: Availability


#: This build ships no launcher changes yet: both methods carry honest reasons. FR-2-AC-1's
#: disabled-never-hidden rendering makes that visible instead of mysterious.
BUILD_LAUNCHERS = LauncherAvailability(
    native=Availability(False, "Native connection support is not included in this build yet"),
    web=Availability(False, "Web launch support is not included in this build yet"),
)

#: Same for the Cloud PC management actions service (add-cloudpc-actions, unapplied).
ACTIONS_SERVICE_AVAILABILITY = Availability(
    False, "Cloud PC management actions are not included in this build yet"
)

#: §7.4 mechanism/data split: statuses known to permit connection. The full status × action
#: matrix is outstanding (G-19) -- until it lands, only the one status Microsoft documents as
#: the steady usable state is connectable, and EVERYTHING else (including unrecognized values)
#: fails closed. ASSUMED pending the G-19 enumeration against v1.0 and a live tenant.
CONNECTABLE_CLOUDPC_STATUSES = frozenset({"provisioned"})


# --- rows and groups --------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountRow:
    """One entry in the account switcher (§4.1)."""

    home_account_id: str
    label: str
    active: bool
    badge: str | None  # e.g. "reauthentication required"; None when the account is healthy


@dataclass(frozen=True)
class ResourceRow:
    """One resource entry: everything a widget row renders and dispatches (§4.1-§4.3)."""

    key: str
    title: str
    type_label: str  # "Cloud PC" / "Desktop" / "RemoteApp"
    status: str | None  # raw Graph status for the chip; None for AVD bookmarks (no live status)
    methods: dict[ConnectMethod, Availability]
    actions: dict[str, Availability]  # empty for AVD bookmarks (§4.3 is Cloud PC only)


@dataclass(frozen=True)
class ResourceGroup:
    """A provider group ("Windows 365" / "Azure Virtual Desktop"), the AVD ones carrying their
    workspace heading (§4.1)."""

    provider: str
    workspace: str | None
    rows: list[ResourceRow]


# --- §4.4 state selection -----------------------------------------------------------------------


class UiState(enum.Enum):
    """The §4.4 states (plus NORMAL for a populated list)."""

    LOADING = "loading"
    NORMAL = "normal"
    EMPTY = "empty"
    NO_LICENCE = "no_licence"
    CONSENT_REQUIRED = "consent_required"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass(frozen=True)
class StateSelection:
    """What the widget stack should show: the selected state, the entries to render (possibly
    the retained previous list, per FR-1-AC-4), whether they render greyed with connects
    disabled (§4.4 Offline), and the banner/retry affordances."""

    state: UiState
    entries: list[CloudPcEntry] = field(default_factory=list)
    greyed: bool = False
    banner_message: str | None = None
    offer_retry: bool = False


def select_ui_state(
    *,
    refresh_in_flight: bool,
    latest: EnumerationResult | None,
) -> StateSelection:
    """The single §4.4 state-selection function (design.md: one function, exhaustively tested).

    ``latest`` is the most recent refresh outcome as delivered by
    ``CloudPcProvider.add_result_listener`` -- including ``Failed``, which the provider's stored
    last-known-good deliberately never becomes. Error-keeps-list (FR-1-AC-4) falls out of
    ``Failed.previous_entries`` riding on the result itself: a failure after a successful
    enumeration renders those entries with an error/offline banner, never a cleared list.

    Offline vs Error (§4.4, §9): a failure whose cause is plain network unreachability selects
    OFFLINE (greyed list, connects disabled, auto-recover on the next successful poll tick); any
    other failure -- proxy explicitly included, which §9 forbids reporting as plain offline --
    selects ERROR with a retry affordance. Both banners carry the failure's ``user_message``
    only; raw detail stays in logs (§9).
    """
    if latest is None:
        # Before the first outcome ever arrives, the skeleton shows regardless of whether the
        # first refresh has been scheduled yet -- there is nothing truthful to render but that.
        return StateSelection(state=UiState.LOADING)

    if isinstance(latest, Enumerated):
        return StateSelection(state=UiState.NORMAL, entries=list(latest.entries))
    if isinstance(latest, Empty):
        return StateSelection(state=UiState.EMPTY)
    if isinstance(latest, NoLicence):
        return StateSelection(state=UiState.NO_LICENCE, banner_message=NoLicence.user_message)
    if isinstance(latest, ConsentRequired):
        return StateSelection(
            state=UiState.CONSENT_REQUIRED, banner_message=latest.user_message
        )
    if isinstance(latest, Failed):
        entries = list(latest.previous_entries)
        if isinstance(latest.error, graph_client.GraphNetworkError) and not isinstance(
            latest.error, graph_client.GraphProxyError
        ):
            return StateSelection(
                state=UiState.OFFLINE,
                entries=entries,
                greyed=True,
                banner_message=latest.user_message,
            )
        return StateSelection(
            state=UiState.ERROR,
            entries=entries,
            banner_message=latest.user_message,
            offer_retry=True,
        )

    # A result type this build does not recognize fails closed into ERROR (same posture as
    # §7.4's unknown-status rule), rather than guessing at a rendering.
    logger.warning("select_ui_state: unrecognized result type %s", type(latest).__name__)
    return StateSelection(
        state=UiState.ERROR,
        banner_message="Something unexpected happened while refreshing. Try again.",
        offer_retry=True,
    )


# --- account switcher rows and banners ---------------------------------------------------------


#: AuthState → switcher badge text (§4.1). Healthy/transient states carry no badge.
_BADGES: dict[AuthState, str] = {
    AuthState.REAUTH_REQUIRED: "reauthentication required",
    AuthState.DEVICE_CA_BLOCKED: "blocked by device policy",
    AuthState.OFFLINE: "offline",
}


def account_rows(
    accounts: "dict[str, object]", active_account_id: str | None
) -> list[AccountRow]:
    """Build switcher rows from ``AuthManager.accounts`` (§4.1). ``accounts`` maps
    home_account_id → ``AccountAuthState``; typed loosely so the view model needs no import of
    the manager itself."""
    rows = []
    for home_account_id, state in accounts.items():
        login_hint = getattr(state, "login_hint", None)
        auth_state = getattr(state, "state", None)
        rows.append(
            AccountRow(
                home_account_id=home_account_id,
                label=login_hint or home_account_id,
                active=home_account_id == active_account_id,
                badge=_BADGES.get(auth_state),
            )
        )
    return rows


def reauth_banner_text(login_hint: str | None, home_account_id: str) -> str:
    """§4.4's per-account banner wording, verbatim from the spec."""
    who = login_hint or home_account_id
    return f"Your sign-in for {who} has expired. Sign in again."


# --- §7.4 enablement -----------------------------------------------------------------------------


def cloudpc_connect_gate(status: str | None) -> Availability:
    """The §7.4 status gate for connecting to a Cloud PC: data table lookup, unknown fails
    closed with the status named in the reason."""
    if status in CONNECTABLE_CLOUDPC_STATUSES:
        return Availability(True)
    shown = status if status else "unknown"
    return Availability(
        False, f"This Cloud PC's current status ({shown}) does not permit connection"
    )


def connect_methods(
    status_gate: Availability,
    launchers: LauncherAvailability = BUILD_LAUNCHERS,
    *,
    native_override_reason: str | None = None,
) -> dict[ConnectMethod, Availability]:
    """Compose the per-method availability pair -- ALWAYS both methods (FR-2-AC-1's
    disabled-never-hidden is unrepresentable any other way).

    The status gate wins first (a non-connectable resource offers no enabled method regardless
    of launchers, §7.4); then a per-resource native override (Phase 0 AVD bookmarks carry
    :data:`~winonlinux.avd_bookmarks.NATIVE_DISABLED_REASON`); then the build's launcher
    availability.
    """
    if not status_gate.enabled:
        blocked = Availability(False, status_gate.reason)
        return {ConnectMethod.NATIVE: blocked, ConnectMethod.WEB: blocked}

    native = (
        Availability(False, native_override_reason)
        if native_override_reason
        else launchers.native
    )
    return {ConnectMethod.NATIVE: native, ConnectMethod.WEB: launchers.web}


class PendingTransitions:
    """Tracks §7.4's post-action conflict suppression: an invoked action's transition
    suppresses conflicting actions on that resource until the next successful refresh resolves
    the new state. With the G-19 matrix outstanding, every action conflicts with a pending
    transition -- the fail-closed default."""

    def __init__(self) -> None:
        self._pending: dict[str, str] = {}

    def mark(self, resource_key: str, action: str) -> None:
        self._pending[resource_key] = action

    def pending_action(self, resource_key: str) -> str | None:
        return self._pending.get(resource_key)

    def clear_on_refresh(self) -> None:
        """Called on every successful enumeration -- the refresh has resolved the new state."""
        self._pending.clear()


#: §4.3's always-rendered Cloud PC actions, in menu order.
CLOUDPC_ACTIONS = ("restart", "rename", "troubleshoot", "reprovision")

#: Rendered ONLY for admin-capable accounts (FR-5-AC-3: capability-unknown renders not capable;
#: hiding -- not disabling -- is the spec'd behavior for these two, unlike connect methods).
CLOUDPC_ADMIN_ACTIONS = ("restore", "resize")


def cloudpc_actions(
    resource_key: str,
    *,
    admin_capable: bool = False,
    pending: PendingTransitions | None = None,
    service: Availability = ACTIONS_SERVICE_AVAILABILITY,
) -> dict[str, Availability]:
    """Availability for the §4.3 action menu.

    ``admin_capable`` defaults False: no admin-capability signal exists in this build (the
    wids-claim detection is add-cloudpc-actions work, D-7), and FR-5-AC-3 says
    capability-unknown renders not capable -- so Restore/Resize are absent from the returned
    dict entirely unless capability is affirmatively known.
    """
    pending_action = pending.pending_action(resource_key) if pending is not None else None

    def gate() -> Availability:
        if pending_action is not None:
            return Availability(
                False,
                f"Waiting for the next refresh to resolve the pending {pending_action}",
            )
        return service

    names = CLOUDPC_ACTIONS + (CLOUDPC_ADMIN_ACTIONS if admin_capable else ())
    return {name: gate() for name in names}


# --- grouping (§4.1) -----------------------------------------------------------------------------


_KIND_TYPE_LABELS = {"desktop": "Desktop", "remoteapp": "RemoteApp"}


def cloudpc_resource_key(entry: CloudPcEntry) -> str:
    """The Graph ``id``, verbatim -- the sole downstream identifier (FR-1-AC-2)."""
    return entry.id


def avd_resource_key(bookmark: AvdBookmark) -> str:
    """workspace+resource ID, per design.md's preference-store key rule."""
    return f"{bookmark.workspace_id}/{bookmark.resource_id}"


def build_groups(
    entries: list[CloudPcEntry],
    bookmarks: list[AvdBookmark],
    *,
    pending: PendingTransitions | None = None,
    admin_capable: bool = False,
    launchers: LauncherAvailability = BUILD_LAUNCHERS,
) -> list[ResourceGroup]:
    """Compose the §4.1 grouped list: one "Windows 365" group, then one "Azure Virtual Desktop"
    group per workspace (via :func:`~winonlinux.avd_bookmarks.group_by_workspace`). Groups with
    no rows are omitted rather than rendered empty."""
    groups: list[ResourceGroup] = []

    if entries:
        rows = [
            ResourceRow(
                key=cloudpc_resource_key(entry),
                title=entry.display_name or entry.id,
                type_label="Cloud PC",
                status=entry.status or None,
                methods=connect_methods(cloudpc_connect_gate(entry.status), launchers),
                actions=cloudpc_actions(
                    cloudpc_resource_key(entry), admin_capable=admin_capable, pending=pending
                ),
            )
            for entry in entries
        ]
        groups.append(ResourceGroup(provider="Windows 365", workspace=None, rows=rows))

    for workspace_id, workspace_bookmarks in group_by_workspace(bookmarks).items():
        rows = [
            ResourceRow(
                key=avd_resource_key(bookmark),
                title=bookmark.display_name,
                type_label=_KIND_TYPE_LABELS.get(bookmark.kind, bookmark.kind),
                status=None,
                methods=connect_methods(
                    Availability(True),
                    launchers,
                    native_override_reason=NATIVE_DISABLED_REASON,
                ),
                actions={},
            )
            for bookmark in workspace_bookmarks
        ]
        groups.append(
            ResourceGroup(provider="Azure Virtual Desktop", workspace=workspace_id, rows=rows)
        )

    return groups
