"""Graph CloudPC provider: enumeration, typed results, and the section 7.3 refresh lifecycle
(add-cloudpc-enumeration change, tasks.md sections 2 and 3).

This module owns:

- :class:`CloudPcEntry` -- the typed shape one enumerated Windows 365 Cloud PC is parsed into.
- The :class:`EnumerationResult` family -- :class:`Enumerated`, :class:`Empty`, :class:`NoLicence`,
  :class:`ConsentRequired`, :class:`Failed` -- so a caller (the future UI shell) dispatches on
  ``isinstance`` rather than string/attribute sniffing (design.md: "the UI cannot conflate them").
- :class:`CloudPcProvider` -- the paged fetch (FR-1-AC-1), the typed-result mapping (FR-1-AC-3/
  FR-1-AC-4), and the §7.3 refresh lifecycle (foreground polling, pause/resume/stop, and the
  post-action immediate-refresh hook, D-8).

Everything here is built strictly against the ``graph_client`` and ``task_registry`` contracts as
given -- it imports :mod:`winonlinux.graph_client` (a parallel task's own file, not redefined here)
for the actual HTTP/retry/throttle machinery, and :mod:`winonlinux.task_registry` for per-account
cancellation (D-18, FR-3-AC-2).

Status handling (design.md "Open Questions", G-19)
---------------------------------------------------
:attr:`CloudPcEntry.status` carries Graph's raw ``status`` string through completely unmodified.
Mapping a status value to which management actions are enabled is explicitly a *different* change's
job (the status x action matrix, G-19) -- this module does not interpret, validate, or normalize it.

Provisioning-metadata fields (tasks.md 2.2)
--------------------------------------------
Beyond ``id``/``displayName``/``status``, a real Graph ``cloudPC`` resource carries several other
documented fields (``imageDisplayName``, ``provisioningType``, ``managedDeviceName``,
``servicePlanName``, etc. -- see Microsoft Graph's ``cloudPC`` resource type). This module carries
``provisioningType`` and ``imageDisplayName`` through as the "genuinely present, reasonable"
provisioning metadata called for by the task -- both are optional (``None`` when Graph omits them)
rather than required, since a stripped-down or future Graph response should not fail to parse over a
field this module does not itself act on beyond display.

ASSUMED (per this repo's "verify, don't assert" discipline, CLAUDE.md): the exact fixture JSON
shapes in ``tests/fixtures/graph/`` are based on Microsoft's *documented* ``/me/cloudPCs`` response
and error-envelope shapes, not captured from a real tenant -- see the README alongside those
fixtures.

Logging discipline (spec.md §10.7): this module never holds or logs an access token or an
``Authorization`` header value itself -- that stays entirely inside ``graph_client.graph_get_json``.
Log lines here carry only account identifiers, URLs, result-type names, and exception type names.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC
from dataclasses import dataclass

from winonlinux import graph_client
from winonlinux.auth_manager import AccountUnknownError
from winonlinux.task_registry import TaskRegistry

__all__ = [
    "CloudPcEntry",
    "EnumerationResult",
    "Enumerated",
    "Empty",
    "NoLicence",
    "ConsentRequired",
    "Failed",
    "CloudPcProvider",
]

logger = logging.getLogger(__name__)

#: Microsoft Graph v1.0 CloudPC enumeration endpoint (spec.md §5.1, FR-1).
_CLOUDPCS_URL = "https://graph.microsoft.com/v1.0/me/cloudPCs"

#: Default delegated scope this provider requests a token for (constructor default; a caller may
#: override, e.g. if a future change needs a broader scope set for the same provider instance).
_DEFAULT_SCOPES: tuple[str, ...] = ("CloudPC.Read.All",)


# --- Typed entry -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CloudPcEntry:
    """One enumerated Windows 365 Cloud PC.

    ``id`` is the Graph ``id`` retained verbatim (FR-1-AC-2) -- the *only* identifier any later
    caller (web launch, FR-5 management actions) needs; those call sites reuse this value directly
    and issue no second lookup.

    ``status`` is Graph's raw status string, carried through completely unmodified -- see the
    module docstring's "Status handling" note.
    """

    id: str
    display_name: str
    status: str
    provisioning_type: str | None = None
    image_display_name: str | None = None


def _entry_from_raw(raw: dict) -> CloudPcEntry:
    """Convert one raw ``/me/cloudPCs`` ``value[]`` entry to a :class:`CloudPcEntry`.

    ``raw["id"]`` is accessed directly (not ``.get``) and deliberately allowed to raise
    ``KeyError`` if Graph ever omits it: silently substituting a placeholder identifier here would
    be far worse than a loud failure, since ``id`` is the sole identifier every downstream action
    (web launch, FR-5 actions) trusts with no second lookup (FR-1-AC-2). ``refresh_now`` converts
    that loud failure into a typed :class:`Failed` (fix-graph-hardening, audit F-03) rather than
    letting it escape untyped.
    """
    return CloudPcEntry(
        id=raw["id"],
        display_name=raw.get("displayName", ""),
        status=raw.get("status", ""),
        provisioning_type=raw.get("provisioningType"),
        image_display_name=raw.get("imageDisplayName"),
    )


# --- Typed enumeration results -----------------------------------------------------------------


class EnumerationResult(ABC):
    """Common base for every shape :meth:`CloudPcProvider.refresh_now` can return.

    A plain marker base (not itself a dataclass) so a caller dispatches via ``isinstance`` across
    the whole family -- design.md: "the FR-1-AC-3/AC-4 distinctions are values, so the UI cannot
    conflate them."
    """


@dataclass(frozen=True)
class Enumerated(EnumerationResult):
    """One or more Cloud PCs were enumerated successfully, across every page (FR-1-AC-1)."""

    entries: list[CloudPcEntry]


@dataclass(frozen=True)
class Empty(EnumerationResult):
    """The account is licensed but has no Cloud PCs provisioned (FR-1-AC-3) -- NOT an error."""


@dataclass(frozen=True)
class NoLicence(EnumerationResult):
    """Graph returned 404 on ``/me/cloudPCs`` -- no Cloud PC licence assigned (FR-1-AC-3),
    distinct from :class:`Empty`. NOT an error."""


@dataclass(frozen=True)
class ConsentRequired(EnumerationResult):
    """Graph returned 403 -- tenant admin consent is missing (the G-42 fix, closing the gap where
    an unconsented tenant had no distinct handling).

    ``admin_consent_url`` is passed straight through from
    :attr:`graph_client.GraphConsentRequired.admin_consent_url`, which that module documents as
    ``None`` in the common case (a bare Graph GET 403 does not reliably carry what is needed to
    compose a correct admin-consent URL) -- so ``None`` here is expected, not a bug, until a caller
    routes the user through the interactive consent flow instead (``AuthManager.add_account`` /
    ``reauth_from_banner``).
    """

    admin_consent_url: str | None


@dataclass(frozen=True)
class Failed(EnumerationResult):
    """A refresh failed for a transport/throttling/unclassified-Graph-error reason (FR-1-AC-4).

    ``previous_entries`` carries the previously successfully-enumerated entries directly on the
    result object -- a caller needs no side-channel state of its own to render "list stays
    visible, error indicator shown" from this one value. Empty (never ``None``) when there was no
    prior successful enumeration yet.
    """

    error: Exception
    previous_entries: list[CloudPcEntry]


# --- Provider ----------------------------------------------------------------------------------


class CloudPcProvider:
    """Enumerates Windows 365 Cloud PCs for the active account and owns the §7.3 refresh
    lifecycle.

    Every outbound Graph call goes through :func:`winonlinux.graph_client.graph_get_json`, which
    itself performs silent token acquisition before every attempt (FR-4-AC-1) -- this class never
    touches ``auth_manager`` directly, only passes it through.

    Polling design note: this provider tracks **one** active periodic-poll loop at a time (the
    active account's), matching how it is actually used -- the app has exactly one foregrounded,
    polled account. :meth:`start_polling` replaces any previously running poll loop for a
    *different* account; calling it again for the *same* already-polling account is a no-op
    (idempotent), matching :meth:`resume_polling`'s documented idempotency.
    """

    def __init__(
        self,
        *,
        auth_manager,
        task_registry: TaskRegistry,
        scopes: tuple[str, ...] = _DEFAULT_SCOPES,
        poll_interval_seconds: float = 60.0,
    ) -> None:
        self._auth_manager = auth_manager
        self._task_registry = task_registry
        self._scopes: tuple[str, ...] = tuple(scopes)
        self._poll_interval_seconds = poll_interval_seconds

        # Keyed by home_account_id (FR-3 isolation; fix-account-lifecycle, audit F-04): one
        # account's entries must never appear as another account's "previous results" after a
        # switch. A Failed result never overwrites an account's entry -- see refresh_now.
        self._last_results: dict[str, EnumerationResult] = {}

        # The single currently-running poll task (if any) and which account it is polling for.
        # Cancelling this task directly (never via task_registry.cancel_group, which would cancel
        # ALL of that account's in-flight work) is what pause_polling/stop_polling do.
        self._poll_task: "asyncio.Task[None] | None" = None
        self._poll_account_id: str | None = None

    def last_result_for(self, home_account_id: str) -> EnumerationResult | None:
        """The last non-``Failed`` result recorded for ``home_account_id``, or ``None`` before
        that account's first refresh completes. Strictly per-account (FR-3): a reader never
        receives another account's data (fix-account-lifecycle, audit F-04)."""
        return self._last_results.get(home_account_id)

    # -- core paged fetch (tasks.md 2.1-2.3) -----------------------------------------------------

    async def refresh_now(self, home_account_id: str) -> EnumerationResult:
        """Fetch every page of ``/me/cloudPCs`` for ``home_account_id`` and return a typed result.

        Paging (FR-1-AC-1): follows ``@odata.nextLink`` from each page's JSON body to exhaustion,
        accumulating every page's ``value`` entries before returning anything -- if any page after
        the first fails, the WHOLE refresh is :class:`Failed`, never a partial :class:`Enumerated`.

        Typed mapping (FR-1-AC-3, FR-1-AC-4, G-42):

        - Accumulated list empty after exhausting paging -> :class:`Empty`.
        - ``graph_client.GraphNotFound`` (Graph 404) -> :class:`NoLicence`.
        - ``graph_client.GraphConsentRequired`` (Graph 403) -> :class:`ConsentRequired`.
        - Any other ``graph_client.GraphError`` (proxy/network/throttled/unclassified) ->
          :class:`Failed`, carrying whatever entries the last *successful* :class:`Enumerated`
          result held (``[]`` if there has never been one).
        - A 200 page violating the documented shape (entry missing ``id``, non-list ``value``) ->
          :class:`Failed` likewise, never an untyped ``KeyError``/``TypeError`` escaping to the
          caller (spec.md §9 "beta contract change"; fix-graph-hardening, audit F-03).

        The per-account entry read back via :meth:`last_result_for` is updated on every
        non-``Failed`` outcome, and left untouched on ``Failed`` -- so a subsequent failure's
        ``previous_entries`` is always derived from the SAME account's last genuinely successful
        enumeration, never an intervening failure and never another account's data (FR-3;
        fix-account-lifecycle, audit F-04).

        An :class:`~winonlinux.auth_manager.AuthError` subclass raised by silent token acquisition
        (inside ``graph_get_json``) propagates out of this method completely uncaught -- that is a
        caller-level concern (e.g. show a ReauthRequired banner), not one of this method's typed
        results.
        """
        entries: list[CloudPcEntry] = []
        url: str | None = _CLOUDPCS_URL

        try:
            while url is not None:
                page = await graph_client.graph_get_json(
                    url,
                    auth_manager=self._auth_manager,
                    home_account_id=home_account_id,
                    scopes=list(self._scopes),
                )
                value = page.get("value", [])
                if not isinstance(value, list):
                    raise TypeError(
                        f"/me/cloudPCs 'value' is {type(value).__name__}, not a list"
                    )
                entries.extend(_entry_from_raw(raw) for raw in value)
                url = page.get("@odata.nextLink")
        except graph_client.GraphNotFound:
            logger.info("cloudpc_provider: account %s has no Cloud PC licence (404)", home_account_id)
            result: EnumerationResult = NoLicence()
            self._last_results[home_account_id] = result
            return result
        except graph_client.GraphConsentRequired as exc:
            logger.warning(
                "cloudpc_provider: account %s requires tenant admin consent (403)", home_account_id
            )
            result = ConsentRequired(admin_consent_url=exc.admin_consent_url)
            self._last_results[home_account_id] = result
            return result
        except graph_client.GraphError as exc:
            previous = self._previous_entries(home_account_id)
            logger.warning(
                "cloudpc_provider: refresh for account %s failed (%s); keeping %d previous entr%s",
                home_account_id,
                type(exc).__name__,
                len(previous),
                "y" if len(previous) == 1 else "ies",
            )
            return Failed(error=exc, previous_entries=list(previous))
        except (KeyError, TypeError, ValueError) as exc:
            # A 200 page whose shape violates the documented /me/cloudPCs contract (entry missing
            # `id`, non-list `value`, ...) must stay inside the typed result taxonomy -- spec.md §9
            # "beta contract change" row (fix-graph-hardening, audit F-03). One malformed entry
            # fails the whole refresh (never a partial Enumerated), previous entries retained.
            previous = self._previous_entries(home_account_id)
            logger.warning(
                "cloudpc_provider: refresh for account %s hit an unexpected /me/cloudPCs shape "
                "(%s: %s) -- possible Graph contract change; keeping %d previous entr%s",
                home_account_id,
                type(exc).__name__,
                exc,
                len(previous),
                "y" if len(previous) == 1 else "ies",
            )
            return Failed(error=exc, previous_entries=list(previous))

        if not entries:
            result = Empty()
        else:
            result = Enumerated(entries=entries)
        self._last_results[home_account_id] = result
        return result

    def _previous_entries(self, home_account_id: str) -> list[CloudPcEntry]:
        prior = self._last_results.get(home_account_id)
        return prior.entries if isinstance(prior, Enumerated) else []

    # -- refresh lifecycle (§7.3, tasks.md 3.1-3.3) ----------------------------------------------

    def start_polling(self, home_account_id: str) -> None:
        """Begin the 60s (by default) foreground poll loop for ``home_account_id``.

        The loop is scheduled as a task inside ``task_registry.get_or_create_group(home_account_id)``
        (D-18) -- so an account switch's ``task_registry.cancel_group(home_account_id)`` cancels it
        along with everything else that account owns (FR-3-AC-2), with no separate cancellation
        logic needed here.

        Idempotent: calling this again while already polling for the *same* account is a no-op.
        Calling it for a *different* account first stops the previous poll loop (via
        :meth:`pause_polling`'s same task-level cancel, not ``cancel_group``) before starting the
        new one -- this provider tracks one active poll loop at a time (see class docstring).
        """
        if self._is_polling_active(home_account_id):
            return

        self._cancel_current_poll_task()

        handle = self._task_registry.get_or_create_group(home_account_id)
        self._poll_task = handle.create_task(
            self._poll_loop(home_account_id), name=f"cloudpc-poll-{home_account_id}"
        )
        self._poll_account_id = home_account_id

    def pause_polling(self) -> None:
        """Stop the periodic loop without cancelling anything else in the account's task group.

        Cancels only this provider's own tracked poll :class:`asyncio.Task` handle -- deliberately
        NOT ``task_registry.cancel_group(...)``, which would cancel *every* in-flight task that
        account owns (e.g. a manual refresh or an in-flight action), not just background polling.
        ``_poll_account_id`` is intentionally left set (not cleared) so a subsequent
        :meth:`resume_polling` call and any external state inspection can still tell which account
        was paused; :func:`stop_polling` is the variant that clears it.
        """
        self._cancel_current_poll_task()

    def resume_polling(self, home_account_id: str) -> None:
        """Restart polling for ``home_account_id`` (§7.3 "resumes on return to foreground").

        Idempotent: a no-op if polling for this exact account is already running -- delegates to
        :meth:`start_polling`, which carries that same guarantee.
        """
        self.start_polling(home_account_id)

    def stop_polling(self) -> None:
        """Tear down polling for good (e.g. on sign-out), as opposed to a foreground/background
        pause that is expected to resume.

        Functionally identical to :meth:`pause_polling` today (both simply cancel the one tracked
        poll task) -- the two are kept as separate, distinctly-named methods because their intent
        differs (transient vs. permanent) even though nothing currently needs to *act* differently
        between them; ``stop_polling`` additionally clears ``_poll_account_id`` since there is no
        expectation of a matching :meth:`resume_polling` call afterward, so there is nothing worth
        remembering "which account was paused" for.
        """
        self._cancel_current_poll_task()
        self._poll_account_id = None

    async def refresh_after_action(self, home_account_id: str) -> EnumerationResult:
        """Immediate-refresh hook for post-action status updates (§7.3, D-8, tasks.md 3.3).

        A thin, separately-named alias for :meth:`refresh_now` -- it exists purely so a future
        ``add-cloudpc-actions`` change has an obvious, stable entry point to call after invoking a
        management action, rather than reaching for ``refresh_now`` and wondering whether that is
        really "the" post-action refresh method. Per D-8, action-completion observation reuses this
        same refresh path rather than separate operation-polling machinery.
        """
        return await self.refresh_now(home_account_id)

    # -- polling internals ------------------------------------------------------------------------

    def _is_polling_active(self, home_account_id: str) -> bool:
        return (
            self._poll_account_id == home_account_id
            and self._poll_task is not None
            and not self._poll_task.done()
        )

    def _cancel_current_poll_task(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = None

    async def _poll_loop(self, home_account_id: str) -> None:
        """The repeating body scheduled by :meth:`start_polling`.

        Sleeps a full interval before each refresh (rather than refreshing immediately on start):
        the immediate enumeration on sign-in/account-switch/manual-refresh (§7.3) is already
        triggered separately by whatever drives this provider (tasks.md 3.1) -- an immediate
        refresh here too would double up on that first call. ``asyncio.sleep`` is used (never
        blocking sleep) so this never stalls the event loop (D-18).

        Any exception an individual :meth:`refresh_now` call raises -- including a
        :class:`~winonlinux.auth_manager.AuthError` subclass, which ``refresh_now`` itself lets
        propagate to a direct caller -- is caught and logged at WARNING here rather than allowed to
        kill the whole polling loop; :class:`asyncio.CancelledError` is a ``BaseException``, not an
        ``Exception``, so it is unaffected by this and still cancels the loop normally.
        """
        while True:
            await asyncio.sleep(self._poll_interval_seconds)
            try:
                await self.refresh_now(home_account_id)
            except AccountUnknownError:
                # The account was signed out from under this loop. Terminal, not retryable:
                # ending the task here is the backstop for any path that starts polling without
                # a matching teardown (fix-account-lifecycle, audit F-05) -- the primary teardown
                # is AuthManager.sign_out destroying the account's task group.
                logger.info(
                    "cloudpc_provider: account %s is no longer signed in; ending its poll loop",
                    home_account_id,
                )
                return
            except Exception:  # noqa: BLE001 - deliberately broad, see docstring
                logger.warning(
                    "cloudpc_provider: periodic refresh for account %s failed; previous result "
                    "(if any) remains in place",
                    home_account_id,
                    exc_info=True,
                )
