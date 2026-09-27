"""The GTK4/libadwaita widget layer (add-ui-shell change, tasks.md sections 2-5).

Thin by design: every decision -- grouping, §4.4 state selection, §7.4 enablement, badge and
banner text -- is computed in :mod:`winonlinux.ui_models` (widget-free, CI-tested); this module
renders whatever those models select and dispatches user intent back into the auth manager and
provider. Per §11.2, the widget behavior itself is verified manually (tasks.md 5.3); no unit
test targets this file (§13.4: no display server in CI).

Threading: everything here runs on the one shared GTK/asyncio thread (D-18), so the auth-state
and result listeners registered below are invoked directly on the UI thread -- no idle_add
marshalling -- and every blocking or slow operation is scheduled as a task under the owning
account's task group (or the app group), never run inline in a signal handler.

Split-button rendering (§4.2, FR-2-AC-1): composed as a linked primary button + menu button
whose popover lists BOTH methods, each individually sensitive with its reason as tooltip text.
design.md pre-authorizes this composition ("the requirement is the stated reason being visible,
not the tooltip mechanism specifically") -- the menu button stays sensitive even when both
methods are disabled, so the reasons remain discoverable.
"""

from __future__ import annotations

import asyncio
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402  (require_version must precede this import)

from winonlinux import ui_models  # noqa: E402
from winonlinux.auth_cache import KeyringUnavailable  # noqa: E402
from winonlinux.auth_manager import SignInCancelled  # noqa: E402
from winonlinux.cloudpc_actions import (  # noqa: E402
    POST_ACTION_REFRESH_TIMEOUT_SECONDS,
    Accepted,
    ConsentRequired as ActionConsentRequired,
    ContractError,
)
from winonlinux.cloudpc_provider import Enumerated  # noqa: E402
from winonlinux.task_registry import run_blocking  # noqa: E402
from winonlinux.web_launcher import BrowserSpawnFailed  # noqa: E402
from winonlinux.ui_models import ConnectMethod, UiState  # noqa: E402

__all__ = ["MainWindow"]

logger = logging.getLogger(__name__)

_METHOD_LABELS = {
    ConnectMethod.NATIVE: "Connect (Native)",
    ConnectMethod.WEB: "Connect (Web browser)",
}

_ACTION_LABELS = {
    "restart": "Restart",
    "rename": "Rename",
    "troubleshoot": "Troubleshoot",
    "reprovision": "Reset (Reprovision)",
    "restore": "Restore",
    "resize": "Resize",
}

_STATUS_PAGES: dict[UiState, tuple[str, str, str]] = {
    # state -> (icon-name, title, description). The two empty states are DISTINCT (FR-1-AC-3)
    # and neither carries error styling; consent-required hosts the guided flow (§9).
    UiState.EMPTY: (
        "folder-symbolic",
        "No resources assigned",
        "No Cloud PCs or AVD resources are assigned to this account.",
    ),
    UiState.NO_LICENCE: (
        "dialog-information-symbolic",
        "No Cloud PC licence",
        "No Cloud PC is assigned to this account. If you expect one, contact your administrator.",
    ),
    UiState.CONSENT_REQUIRED: (
        "dialog-password-symbolic",
        "Administrator approval needed",
        "An administrator of this organization must approve this app before Cloud PCs can be "
        "listed. Ask them to grant consent, then refresh.",
    ),
}


class MainWindow(Adw.ApplicationWindow):
    """The §4.1 main window: account switcher, refresh, banners, grouped resource list."""

    def __init__(self, *, application) -> None:
        super().__init__(application=application, title="WinOnLinux")
        self.set_default_size(760, 640)

        self._app = application
        self._auth = application.auth_manager
        self._provider = application.cloudpc_provider
        self._bookmark_store = application.avd_bookmarks
        self._task_registry = application.task_registry
        self._method_prefs = application.method_prefs

        self._web_launcher = application.web_launcher
        self._actions = application.cloudpc_actions
        self._bookmarks: list = []
        self._latest_by_account: dict[str, object] = {}
        self._refresh_in_flight = False
        self._pending = ui_models.PendingTransitions()
        # Resource keys with a launch in flight -- drives the §4.2 per-entry spinner until the
        # browser is spawned (or the native session window appears, once native exists).
        self._launching_keys: set[str] = set()
        # D-7 admin capability per account, evaluated asynchronously on account activation;
        # absent (= unknown) renders not capable (FR-5-AC-3).
        self._admin_capable: dict[str, bool] = {}

        self._build_widgets()

        # Listeners fire on this same thread (D-18); rendering directly is safe. They are the
        # event-driven core: no UI-side polling timer anywhere (add-state-change-listeners).
        self._auth.add_auth_state_listener(self._on_auth_state_changed)
        self._auth.add_active_account_listener(self._on_active_account_changed)
        self._provider.add_result_listener(self._on_result)

        self._schedule_startup_loads()
        self._render()

    # -- widget construction ----------------------------------------------------------------

    def _build_widgets(self) -> None:
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        self._account_button = Gtk.MenuButton()
        self._account_button.set_tooltip_text("Accounts")
        self._account_popover = Gtk.Popover()
        self._account_button.set_popover(self._account_popover)
        header.pack_start(self._account_button)

        refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_button.set_tooltip_text("Refresh")
        refresh_button.connect("clicked", lambda *_: self._trigger_refresh())
        header.pack_end(refresh_button)
        toolbar_view.add_top_bar(header)

        self._toast_overlay = Adw.ToastOverlay()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Per-account, non-blocking (§4.4): a banner above the list, never a modal.
        self._reauth_banner = Adw.Banner()
        self._reauth_banner.set_button_label("Sign in again")
        self._reauth_banner.connect("button-clicked", lambda *_: self._trigger_reauth())
        content.append(self._reauth_banner)

        # Offline / error / keyring-refusal messaging share one banner slot; the §4.4 state
        # decides which text (and whether Retry) it carries.
        self._status_banner = Adw.Banner()
        self._status_banner.connect("button-clicked", lambda *_: self._trigger_refresh())
        content.append(self._status_banner)

        self._stack = Gtk.Stack()

        loading_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER
        )
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        loading_box.append(spinner)
        loading_box.append(Gtk.Label(label="Loading resources…"))
        self._stack.add_named(loading_box, "loading")

        self._status_page = Adw.StatusPage()
        self._stack.add_named(self._status_page, "status")

        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._list_box.set_margin_top(18)
        self._list_box.set_margin_bottom(18)
        self._list_box.set_margin_start(18)
        self._list_box.set_margin_end(18)
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_child(self._list_box)
        self._stack.add_named(scrolled, "list")

        content.append(self._stack)
        self._toast_overlay.set_child(content)
        toolbar_view.set_content(self._toast_overlay)
        self.set_content(toolbar_view)

    # -- startup ------------------------------------------------------------------------------

    def _schedule_startup_loads(self) -> None:
        """Load bookmarks and method preferences off the loop thread (F-21 convention), then
        re-render with them in place."""

        async def load() -> None:
            try:
                self._bookmarks = await run_blocking(self._bookmark_store.load)
                await run_blocking(self._method_prefs.load)
            except Exception:  # noqa: BLE001 - startup convenience data; never fatal
                logger.warning("ui_shell: startup store loads failed", exc_info=True)
            self._render()

        self._task_registry.get_or_create_group(None).create_task(
            load(), name="ui-startup-loads"
        )

    # -- listeners (event-driven rendering; no polling) -----------------------------------------

    def _on_auth_state_changed(self, home_account_id: str, new_state) -> None:
        logger.debug("ui_shell: account %s -> %s", home_account_id, new_state)
        self._render()

    def _on_active_account_changed(self, home_account_id: str) -> None:
        self._refresh_in_flight = True
        self._schedule_capability_evaluation(home_account_id)
        self._render()

    def _schedule_capability_evaluation(self, home_account_id: str) -> None:
        """D-7: evaluate admin capability off the render path; re-render when known so
        Restore/Resize appear for admin-capable accounts (FR-5-AC-3)."""

        async def evaluate() -> None:
            capable = await self._actions.evaluate_admin_capability(home_account_id)
            if self._admin_capable.get(home_account_id) != capable:
                self._admin_capable[home_account_id] = capable
                self._render()

        self._task_registry.get_or_create_group(home_account_id).create_task(
            evaluate(), name="ui-admin-capability"
        )

    def _on_result(self, home_account_id: str, result) -> None:
        self._latest_by_account[home_account_id] = result
        if isinstance(result, Enumerated):
            self._pending.clear_on_refresh()
        if home_account_id == self._auth.active_account_id:
            self._refresh_in_flight = False
        self._render()

    # -- user intent dispatch --------------------------------------------------------------------

    def _trigger_refresh(self) -> None:
        """Manual refresh (§7.3's third trigger -- the half add-cloudpc-enumeration left for
        this change): scheduled under the active account's group so a switch cancels it."""
        account_id = self._auth.active_account_id
        if account_id is None:
            self._toast("Sign in to load resources")
            return
        self._refresh_in_flight = True
        self._render()

        async def refresh() -> None:
            try:
                await self._provider.refresh_now(account_id)
            except Exception:  # noqa: BLE001 - outcome (incl. auth errors) surfaces via listeners
                logger.warning("ui_shell: manual refresh failed", exc_info=True)
                self._refresh_in_flight = False
                self._render()

        self._task_registry.get_or_create_group(account_id).create_task(
            refresh(), name="ui-manual-refresh"
        )

    def _trigger_reauth(self) -> None:
        account_id = self._auth.active_account_id
        if account_id is None:
            return

        async def reauth() -> None:
            try:
                await self._auth.reauth_from_banner(account_id)
                self._trigger_refresh()
            except SignInCancelled:
                pass  # §9: return to prior state, no error dialog
            except Exception as exc:  # noqa: BLE001 - typed AuthErrors carry user_message
                self._toast(getattr(exc, "user_message", "Sign-in did not complete. Try again."))
                logger.warning("ui_shell: re-auth failed", exc_info=True)

        self._task_registry.get_or_create_group(account_id).create_task(
            reauth(), name="ui-reauth"
        )

    def _switch_account(self, home_account_id: str) -> None:
        async def switch() -> None:
            await self._auth.switch_active_account(home_account_id)

        self._task_registry.get_or_create_group(None).create_task(
            switch(), name="ui-switch-account"
        )
        self._account_popover.popdown()

    def _add_account(self) -> None:
        async def add() -> None:
            try:
                await self._auth.add_account()
                self._toast("Account added")
            except SignInCancelled:
                pass
            except Exception as exc:  # noqa: BLE001 - typed AuthErrors carry user_message
                self._toast(getattr(exc, "user_message", "Sign-in did not complete. Try again."))
                logger.warning("ui_shell: add account failed", exc_info=True)

        self._task_registry.get_or_create_group(None).create_task(add(), name="ui-add-account")
        self._account_popover.popdown()

    def _sign_out(self, home_account_id: str) -> None:
        async def sign_out() -> None:
            try:
                await self._auth.sign_out(home_account_id)
                await run_blocking(self._method_prefs.forget_account, home_account_id)
                self._latest_by_account.pop(home_account_id, None)
            except Exception as exc:  # noqa: BLE001
                self._toast(getattr(exc, "user_message", "Sign-out failed. Try again."))
                logger.warning("ui_shell: sign-out failed", exc_info=True)
            self._render()

        # Scheduled OUTSIDE the account's own group: sign_out destroys that group last
        # (fix-account-lifecycle) and must not cancel itself mid-teardown.
        self._task_registry.get_or_create_group(None).create_task(
            sign_out(), name="ui-sign-out"
        )
        self._account_popover.popdown()

    def _launch(self, resource_key: str, method: ConnectMethod, *, kind: str) -> None:
        """Dispatch a connect. Reachable only through an enabled method entry: web dispatches
        through the web launcher (add-web-launcher); native has no launcher in this build, so
        an arrival here is fail-safe plumbing that presents the FR-2-AC-5 fallback surface."""
        account_id = self._auth.active_account_id
        if account_id is None:
            return

        if method is ConnectMethod.NATIVE:
            self._method_prefs.record_use(account_id, resource_key, method)  # FR-2-AC-2
            self._present_launch_failure(
                resource_key,
                f"{_METHOD_LABELS[method]} is not available in this build yet",
                offer_web_fallback=True,
                kind=kind,
            )
            return

        bookmark = self._bookmark_for(resource_key) if kind != "cloudpc" else None
        if bookmark is not None and self._web_launcher.needs_second_remoteapp_warning(bookmark):
            # §12 risk 9: a second RemoteApp web tab from the same host pool disconnects the
            # first -- warn, and launch only on explicit confirmation.
            self._confirm_second_remoteapp(resource_key, kind)
            return

        self._start_web_launch(resource_key, kind)

    def _bookmark_for(self, resource_key: str):
        for bookmark in self._bookmarks:
            if ui_models.avd_resource_key(bookmark) == resource_key:
                return bookmark
        return None

    def _confirm_second_remoteapp(self, resource_key: str, kind: str) -> None:
        dialog = Adw.AlertDialog(
            heading="Disconnect the other RemoteApp?",
            body=(
                "Opening another RemoteApp from this workspace disconnects the RemoteApp "
                "session already running in your browser."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("launch", "Open anyway")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response: str) -> None:
            if response == "launch":
                self._start_web_launch(resource_key, kind)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _start_web_launch(self, resource_key: str, kind: str) -> None:
        """Resolve the launch URL and hand it to the browser, with the §4.2 spinner shown on
        the entry until the spawn completes and the §9 copyable-URL toast on spawn failure."""
        account_id = self._auth.active_account_id
        if account_id is None:
            return
        self._method_prefs.record_use(account_id, resource_key, ConnectMethod.WEB)  # FR-2-AC-2
        self._launching_keys.add(resource_key)
        self._render()

        async def launch() -> None:
            try:
                if kind == "cloudpc":
                    url = await self._web_launcher.resolve_cloudpc_url(resource_key, account_id)
                else:
                    bookmark = self._bookmark_for(resource_key)
                    if bookmark is None:
                        self._toast("This resource is no longer available. Refresh and retry.")
                        return
                    url = self._web_launcher.resolve_avd_url(bookmark, account_id)
                await self._web_launcher.open_in_browser(url)
                if kind == "remoteapp":
                    bookmark = self._bookmark_for(resource_key)
                    if bookmark is not None:
                        self._web_launcher.note_remoteapp_launch(bookmark)
            except BrowserSpawnFailed as exc:
                self._present_spawn_failure(exc)
            except Exception as exc:  # noqa: BLE001 - typed AuthErrors carry user_message
                self._toast(getattr(exc, "user_message", "The connection could not be started."))
                logger.warning("ui_shell: web launch failed", exc_info=True)
            finally:
                self._launching_keys.discard(resource_key)
                self._render()

        self._task_registry.get_or_create_group(account_id).create_task(
            launch(), name="ui-web-launch"
        )

    def _present_spawn_failure(self, failure: BrowserSpawnFailed) -> None:
        """§9: browser fails to spawn -> error toast offering the URL for manual copy."""
        toast = Adw.Toast(title=failure.user_message)
        toast.set_button_label("Copy link")
        toast.set_timeout(0)  # sticks around until dismissed; the user needs time to copy

        def copy(*_args) -> None:
            self.get_clipboard().set(failure.url)

        toast.connect("button-clicked", copy)
        self._toast_overlay.add_toast(toast)

    def _present_launch_failure(
        self, resource_key: str, reason: str, *, offer_web_fallback: bool, kind: str
    ) -> None:
        """FR-2-AC-5's shared failure surface: the reason plus, for a native failure, a
        one-action web fallback on the same surface."""
        toast = Adw.Toast(title=reason)
        if offer_web_fallback:
            toast.set_button_label("Connect (Web)")
            toast.connect(
                "button-clicked",
                lambda *_: self._start_web_launch(resource_key, kind),
            )
        self._toast_overlay.add_toast(toast)

    def _invoke_action(self, resource_key: str, action: str) -> None:
        """Route a Cloud PC action (reachable only through an enabled entry, §7.4): rename
        prompts for the new name, reprovision goes through its FR-5-AC-2 confirmation gate,
        everything else dispatches directly."""
        if action == "reprovision":
            self._confirm_reprovision(resource_key)
            return
        if action == "rename":
            self._prompt_rename(resource_key)
            return
        self._run_action(resource_key, action)

    def _prompt_rename(self, resource_key: str) -> None:
        dialog = Adw.AlertDialog(heading="Rename Cloud PC", body="Enter the new display name.")
        entry = Gtk.Entry(activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response: str) -> None:
            new_name = entry.get_text().strip()
            if response == "rename" and new_name:
                self._run_action(resource_key, "rename", payload={"displayName": new_name})

        dialog.connect("response", on_response)
        dialog.present(self)

    def _confirm_reprovision(self, resource_key: str) -> None:
        """FR-5-AC-2: explicit checked confirmation; cancel dispatches nothing."""
        dialog = Adw.AlertDialog(
            heading="Reset this Cloud PC?",
            body=(
                "Resetting (reprovisioning) wipes this Cloud PC and rebuilds it from scratch. "
                "Everything stored on it is permanently lost."
            ),
        )
        check = Gtk.CheckButton(label="I understand this Cloud PC will be wiped and rebuilt")
        dialog.set_extra_child(check)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reprovision", "Reset (Reprovision)")
        dialog.set_response_appearance("reprovision", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_enabled("reprovision", False)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        check.connect(
            "toggled",
            lambda button: dialog.set_response_enabled("reprovision", button.get_active()),
        )

        def on_response(_dialog, response: str) -> None:
            if response == "reprovision":
                self._run_action(resource_key, "reprovision", confirmed=True)
            # Anything else -- cancel, close, Esc -- dispatches nothing (FR-5-AC-2): the
            # service call below is never reached, so no network I/O ever happens.

        dialog.connect("response", on_response)
        dialog.present(self)

    def _run_action(
        self,
        resource_key: str,
        action: str,
        *,
        payload: dict | None = None,
        confirmed: bool = False,
    ) -> None:
        """Dispatch through the Management Action service (add-cloudpc-actions) and surface the
        closed outcome family per §9: progress via pending suppression + toast, completion via
        the immediate refresh the service schedules (D-8, FR-5-AC-1)."""
        account_id = self._auth.active_account_id
        if account_id is None:
            return

        self._pending.mark(resource_key, action)  # §7.4 conflict suppression until refresh
        self._render()

        async def run() -> None:
            try:
                if action == "reprovision":
                    outcome = await self._actions.invoke_reprovision(
                        account_id, resource_key, confirmed=confirmed
                    )
                else:
                    outcome = await self._actions.invoke_action(
                        account_id, resource_key, action, payload=payload
                    )
            except Exception as exc:  # noqa: BLE001 - typed AuthErrors carry user_message
                self._pending.clear(resource_key)
                self._toast(getattr(exc, "user_message", "The action could not be completed."))
                logger.warning("ui_shell: %s failed", action, exc_info=True)
                self._render()
                return
            self._handle_action_outcome(resource_key, action, outcome)

        self._task_registry.get_or_create_group(account_id).create_task(
            run(), name=f"ui-action-{action}"
        )

    def _handle_action_outcome(self, resource_key: str, action: str, outcome) -> None:
        if isinstance(outcome, Accepted):
            self._toast(f"{_ACTION_LABELS.get(action, action)}: {outcome.user_message}")
            self._schedule_pending_timeout(resource_key)
            return

        # Every non-accepted outcome lifts the suppression -- no transition was triggered.
        self._pending.clear(resource_key)
        if isinstance(outcome, ActionConsentRequired):
            self._present_action_consent_required(outcome)
        elif isinstance(outcome, ContractError):
            self._toast(outcome.user_message)  # the gate now disables this action (FR-5-AC-5)
        else:
            self._toast(outcome.user_message)
        self._render()

    def _schedule_pending_timeout(self, resource_key: str) -> None:
        """D-8's bounded wait: if no clean refresh resolves the transition within the stated
        timeout, re-read state (trigger a refresh) rather than staying suppressed forever."""
        account_id = self._auth.active_account_id

        async def watchdog() -> None:
            await asyncio.sleep(POST_ACTION_REFRESH_TIMEOUT_SECONDS)
            if self._pending.pending_action(resource_key) is not None:
                logger.info(
                    "ui_shell: post-action refresh timed out for %s; re-reading state",
                    resource_key,
                )
                self._pending.clear(resource_key)
                self._trigger_refresh()

        self._task_registry.get_or_create_group(account_id).create_task(
            watchdog(), name="ui-action-timeout"
        )

    def _present_action_consent_required(self, outcome) -> None:
        """FR-5-AC-4's guided surface: explain that a tenant admin must approve, offering the
        admin-consent URL for forwarding when one exists."""
        dialog = Adw.AlertDialog(
            heading="Administrator approval needed",
            body=(
                outcome.user_message
                + (
                    "\n\nCopy the approval link and forward it to an administrator."
                    if outcome.admin_consent_url
                    else ""
                )
            ),
        )
        dialog.add_response("close", "Close")
        if outcome.admin_consent_url:
            dialog.add_response("copy", "Copy approval link")

            def on_response(_dialog, response: str) -> None:
                if response == "copy":
                    self.get_clipboard().set(outcome.admin_consent_url)

            dialog.connect("response", on_response)
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.present(self)

    # -- rendering ---------------------------------------------------------------------------

    def _toast(self, message: str) -> None:
        self._toast_overlay.add_toast(Adw.Toast(title=message))

    def _render(self) -> None:
        self._render_account_switcher()
        self._render_banners()
        self._render_body()

    def _render_account_switcher(self) -> None:
        rows = ui_models.account_rows(self._auth.accounts, self._auth.active_account_id)
        active = next((row for row in rows if row.active), None)
        self._account_button.set_label(active.label if active else "Sign in")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for row in rows:
            label = row.label + (f"  ⚠ {row.badge}" if row.badge else "")
            button = Gtk.Button(label=("● " if row.active else "") + label)
            button.add_css_class("flat")
            button.connect(
                "clicked", lambda _b, account_id=row.home_account_id: self._switch_account(account_id)
            )
            box.append(button)
        if rows:
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        add_button = Gtk.Button(label="Add account…")
        add_button.add_css_class("flat")
        add_button.connect("clicked", lambda *_: self._add_account())
        box.append(add_button)

        for row in rows:
            sign_out = Gtk.Button(label=f"Sign out {row.label}")
            sign_out.add_css_class("flat")
            sign_out.connect(
                "clicked", lambda _b, account_id=row.home_account_id: self._sign_out(account_id)
            )
            box.append(sign_out)

        self._account_popover.set_child(box)

    def _current_selection(self) -> ui_models.StateSelection:
        account_id = self._auth.active_account_id
        latest = self._latest_by_account.get(account_id) if account_id else None
        return ui_models.select_ui_state(
            refresh_in_flight=self._refresh_in_flight, latest=latest
        )

    def _render_banners(self) -> None:
        # D-2 keyring refusal: start() failed, sign-in is disabled, say so plainly (§9). The
        # stash can land after construction; every render pass re-checks it.
        startup_error = getattr(self._app, "auth_manager_start_error", None)
        account_id = self._auth.active_account_id

        account_state = self._auth.accounts.get(account_id) if account_id else None
        if account_state is not None and account_state.state.name == "REAUTH_REQUIRED":
            self._reauth_banner.set_title(
                ui_models.reauth_banner_text(account_state.login_hint, account_id)
            )
            self._reauth_banner.set_revealed(True)
        else:
            self._reauth_banner.set_revealed(False)

        if isinstance(startup_error, KeyringUnavailable):
            self._status_banner.set_title(
                "Sign-in is unavailable: no secure system keyring could be reached."
            )
            self._status_banner.set_button_label(None)
            self._status_banner.set_revealed(True)
            return

        selection = self._current_selection()
        if selection.state in (UiState.OFFLINE, UiState.ERROR) and selection.banner_message:
            self._status_banner.set_title(selection.banner_message)
            self._status_banner.set_button_label("Retry" if selection.offer_retry else None)
            self._status_banner.set_revealed(True)
        else:
            self._status_banner.set_revealed(False)

    def _render_body(self) -> None:
        selection = self._current_selection()

        if selection.state is UiState.LOADING and self._refresh_in_flight:
            self._stack.set_visible_child_name("loading")
            return

        has_rows = bool(selection.entries) or bool(self._bookmarks)
        if selection.state in _STATUS_PAGES and not has_rows:
            icon, title, description = _STATUS_PAGES[selection.state]
            self._status_page.set_icon_name(icon)
            self._status_page.set_title(title)
            self._status_page.set_description(description)
            self._stack.set_visible_child_name("status")
            return

        if not has_rows and selection.state is UiState.LOADING:
            self._stack.set_visible_child_name("loading")
            return

        self._render_groups(selection)
        self._stack.set_visible_child_name("list")

    def _render_groups(self, selection: ui_models.StateSelection) -> None:
        child = self._list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._list_box.remove(child)
            child = next_child

        account_id = self._auth.active_account_id
        groups = ui_models.build_groups(
            selection.entries,
            self._bookmarks,
            pending=self._pending,
            admin_capable=self._admin_capable.get(account_id, False) if account_id else False,
            action_gate=lambda action: ui_models.Availability(*self._actions.action_gate(action)),
        )

        for group in groups:
            pref_group = Adw.PreferencesGroup()
            pref_group.set_title(group.provider)
            if group.workspace:
                pref_group.set_description(f"Workspace: {group.workspace}")
            for row in group.rows:
                pref_group.add(self._build_row(row, account_id, greyed=selection.greyed))
            self._list_box.append(pref_group)

    def _build_row(self, row: ui_models.ResourceRow, account_id, *, greyed: bool) -> Gtk.Widget:
        action_row = Adw.ActionRow(title=row.title, subtitle=row.type_label)

        if row.key in self._launching_keys:
            action_row.add_suffix(Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER))

        if row.status:
            chip = Gtk.Label(label=row.status, valign=Gtk.Align.CENTER)
            chip.add_css_class("caption")
            chip.add_css_class("dim-label" if greyed else "accent")
            action_row.add_suffix(chip)

        action_row.add_suffix(self._build_connect_control(row, account_id, greyed=greyed))

        if row.actions:
            action_row.add_suffix(self._build_actions_menu(row))

        if greyed:
            action_row.add_css_class("dim-label")  # §4.4 Offline: cached list greyed
        return action_row

    def _build_connect_control(
        self, row: ui_models.ResourceRow, account_id, *, greyed: bool
    ) -> Gtk.Widget:
        """The §4.2 split control: linked primary button (remembered method, FR-2-AC-2) plus a
        dropdown listing BOTH methods, disabled entries carrying their visible reason
        (FR-2-AC-1). §4.4 Offline additionally disables connects wholesale."""
        preferred = (
            self._method_prefs.preferred_method(account_id, row.key)
            if account_id
            else ConnectMethod.WEB
        )
        offline_block = ui_models.Availability(False, "You appear to be offline") if greyed else None

        primary_availability = offline_block or row.methods[preferred]
        primary = Gtk.Button(label=_METHOD_LABELS[preferred])
        primary.set_sensitive(primary_availability.enabled)
        if primary_availability.reason:
            primary.set_tooltip_text(primary_availability.reason)
        primary.connect("clicked", lambda *_: self._launch(row.key, preferred, kind=row.kind))

        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover = Gtk.Popover()
        for method in (ConnectMethod.NATIVE, ConnectMethod.WEB):
            availability = offline_block or row.methods[method]
            entry = Gtk.Button(label=_METHOD_LABELS[method])
            entry.add_css_class("flat")
            entry.set_sensitive(availability.enabled)
            if availability.reason:
                entry.set_tooltip_text(availability.reason)
            entry.connect(
                "clicked",
                lambda _b, m=method: (popover.popdown(), self._launch(row.key, m, kind=row.kind)),
            )
            menu_box.append(entry)
        popover.set_child(menu_box)

        dropdown = Gtk.MenuButton(icon_name="pan-down-symbolic")
        dropdown.set_popover(popover)  # stays sensitive: reasons remain discoverable

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, valign=Gtk.Align.CENTER)
        box.add_css_class("linked")
        box.append(primary)
        box.append(dropdown)
        return box

    def _build_actions_menu(self, row: ui_models.ResourceRow) -> Gtk.Widget:
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover = Gtk.Popover()
        for action, availability in row.actions.items():
            entry = Gtk.Button(label=_ACTION_LABELS.get(action, action))
            entry.add_css_class("flat")
            if action == "reprovision":
                entry.add_css_class("destructive-action")
            entry.set_sensitive(availability.enabled)
            if availability.reason:
                entry.set_tooltip_text(availability.reason)
            entry.connect(
                "clicked",
                lambda _b, a=action: (popover.popdown(), self._invoke_action(row.key, a)),
            )
            menu_box.append(entry)
        popover.set_child(menu_box)

        button = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER)
        button.set_tooltip_text("Actions")
        button.set_popover(popover)
        return button
