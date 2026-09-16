"""Per-account asyncio task-group registry.

Implements the "Single asyncio event loop with per-account task groups" requirement (spec.md,
add-app-foundation change, D-18):

- All asynchronous work belonging to a signed-in account runs inside a group of tasks keyed by
  that account's home account ID. Cancelling one account's group cancels every pending task it
  owns without touching any other account's tasks (FR-3-AC-2).
- Work not tied to any particular account (app startup, the FreeRDP probe, etc.) runs under one
  shared app-scoped group, selected with the :data:`APP_SCOPE` sentinel.
- Blocking calls -- keyring access, subprocess spawns -- MUST never run synchronously on the loop
  thread. :func:`run_blocking` (and the :meth:`TaskRegistry.run_blocking` convenience method)
  dispatch them to the default thread executor via ``loop.run_in_executor``.

Why not a bare ``asyncio.TaskGroup`` per account: ``TaskGroup`` (3.11+) is an async context
manager designed to be entered once and awaited until every task it holds finishes -- it does not
support adding tasks after all existing ones have completed, and it has no supported "cancel this
group but keep the registry object around for later reuse" operation. A long-lived, per-account
registry that accounts sign in and out of repeatedly needs incremental add-after-idle and
reusable cancel/destroy semantics that ``TaskGroup`` does not offer, so this module implements a
small hand-rolled group instead: a plain ``set[asyncio.Task]`` per key, with helpers to add,
cancel, and tear down.

Pure asyncio / standard library only -- this module deliberately imports no ``gi``/GTK so it is
unit-testable in any environment with plain CPython and pytest, no PyGObject required.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Hashable
from typing import Any, TypeVar

__all__ = [
    "APP_SCOPE",
    "AccountId",
    "TaskGroupHandle",
    "TaskRegistry",
    "run_blocking",
]

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: Sentinel key for the one app-scoped task group: work that is not tied to any signed-in
#: account. Passing ``None`` as an account ID also resolves to this group (see
#: :meth:`TaskRegistry.get_or_create_group`), so callers may use whichever spelling reads better
#: at the call site; ``APP_SCOPE`` is provided for callers that want an explicit, importable name
#: rather than a bare ``None`` literal.
APP_SCOPE = "__app_scope__"

#: An account is keyed by its MSAL "home account ID" (or the :data:`APP_SCOPE` sentinel for the
#: one app-scoped group). ``None`` is also accepted at the registry's public API and is
#: normalized to :data:`APP_SCOPE`.
AccountId = Hashable


async def run_blocking(func: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    """Run the blocking callable ``func(*args, **kwargs)`` in the default thread executor.

    This is how every blocking keyring or subprocess call must be dispatched per D-18 -- never
    called synchronously on the event-loop thread, which is shared with GTK's main loop and would
    stall UI event processing for the duration of the call.

    ``loop.run_in_executor`` only accepts positional arguments for the callable, so keyword
    arguments are bound with :func:`functools.partial` first when present.
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        import functools

        func = functools.partial(func, **kwargs)
        return await loop.run_in_executor(None, func, *args)
    return await loop.run_in_executor(None, func, *args)


class TaskGroupHandle:
    """A handle scoped to one account (or the app scope) inside a :class:`TaskRegistry`.

    Not meant to be constructed directly -- obtain one from
    :meth:`TaskRegistry.get_or_create_group`. Holding a handle does not itself keep the group
    alive in the registry's bookkeeping after :meth:`TaskRegistry.destroy_group` removes it; a
    handle obtained before a destroy still lets you inspect (but not meaningfully repopulate) the
    now-detached task set.
    """

    __slots__ = ("account_id", "_registry")

    def __init__(self, account_id: AccountId, registry: "TaskRegistry") -> None:
        self.account_id = account_id
        self._registry = registry

    def create_task(
        self, coro: Awaitable[_T], *, name: str | None = None
    ) -> "asyncio.Task[_T]":
        """Schedule ``coro`` as a task tracked under this handle's account/scope."""
        return self._registry.create_task(self.account_id, coro, name=name)

    async def run_blocking(self, func: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        """Convenience alias for the module-level :func:`run_blocking` helper."""
        return await run_blocking(func, *args, **kwargs)

    def cancel(self) -> None:
        """Cancel every task currently tracked under this account, keeping the group registered."""
        self._registry.cancel_group(self.account_id)

    def destroy(self) -> None:
        """Cancel every task currently tracked under this account and remove the bookkeeping."""
        self._registry.destroy_group(self.account_id)

    @property
    def tasks(self) -> frozenset["asyncio.Task[Any]"]:
        """A snapshot of the tasks currently tracked under this account."""
        return frozenset(self._registry._groups.get(self._key(), ()))

    def _key(self) -> AccountId:
        return self._registry._normalize(self.account_id)


class TaskRegistry:
    """Owns one set of in-flight :class:`asyncio.Task` objects per account, plus one app-scoped set.

    Cancelling or destroying one account's group affects only that account's tasks (spec.md
    scenario "Account task group cancellation is isolated"); every other account's tasks, and the
    app-scoped group's tasks, are left untouched.
    """

    def __init__(self) -> None:
        self._groups: dict[AccountId, set["asyncio.Task[Any]"]] = {}

    @staticmethod
    def _normalize(account_id: AccountId | None) -> AccountId:
        return APP_SCOPE if account_id is None else account_id

    def get_or_create_group(self, account_id: AccountId | None = None) -> TaskGroupHandle:
        """Return a handle scoped to ``account_id`` (or the app scope when omitted/``None``).

        Creating the underlying bookkeeping is idempotent and lazy: calling this repeatedly for
        the same account before any task is created is cheap and returns equivalent handles.
        """
        key = self._normalize(account_id)
        self._groups.setdefault(key, set())
        return TaskGroupHandle(key, self)

    def create_task(
        self,
        account_id: AccountId | None,
        coro: Awaitable[_T],
        *,
        name: str | None = None,
    ) -> "asyncio.Task[_T]":
        """Schedule ``coro`` as a task tracked under ``account_id`` (or the app scope).

        The task removes itself from the tracked set automatically on completion (success,
        exception, or cancellation) so the set never accumulates finished tasks.
        """
        key = self._normalize(account_id)
        task: "asyncio.Task[_T]" = asyncio.ensure_future(coro)
        if name is not None:
            task.set_name(name)
        bucket = self._groups.setdefault(key, set())
        bucket.add(task)
        task.add_done_callback(lambda t, _key=key: self._discard(_key, t))
        return task

    def _discard(self, key: AccountId, task: "asyncio.Task[Any]") -> None:
        bucket = self._groups.get(key)
        if bucket is not None:
            bucket.discard(task)

    def cancel_group(self, account_id: AccountId | None) -> None:
        """Cancel every task currently tracked under ``account_id``, without removing bookkeeping.

        Tasks belonging to any other account (or the app scope, if a different account was
        given) are entirely unaffected. Already-finished tasks are simply not in the set and so
        are no-ops here.
        """
        key = self._normalize(account_id)
        bucket = self._groups.get(key)
        if not bucket:
            return
        for task in list(bucket):
            if not task.done():
                task.cancel()

    def destroy_group(self, account_id: AccountId | None) -> None:
        """Cancel every task tracked under ``account_id`` and remove its bookkeeping entirely.

        A subsequent :meth:`get_or_create_group` for the same ``account_id`` starts from a fresh,
        empty set rather than reusing any state left over from before the destroy.
        """
        self.cancel_group(account_id)
        key = self._normalize(account_id)
        self._groups.pop(key, None)

    def active_account_ids(self) -> frozenset[AccountId]:
        """Account IDs (including :data:`APP_SCOPE` if in use) that currently have bookkeeping."""
        return frozenset(self._groups.keys())

    def pending_count(self, account_id: AccountId | None) -> int:
        """Number of not-yet-done tasks currently tracked under ``account_id``."""
        key = self._normalize(account_id)
        bucket = self._groups.get(key)
        if not bucket:
            return 0
        return sum(1 for task in bucket if not task.done())
