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

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402  (require_version must precede this import)

from winonlinux import ui_models  # noqa: E402
from winonlinux.auth_cache import KeyringUnavailable  # noqa: E402
from winonlinux.auth_manager import SignInCancelled  # noqa: E402
from winonlinux.cloudpc_provider import Enumerated  # noqa: E402
from winonlinux.task_registry import run_blocking  # noqa: E402
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

        self._bookmarks: list = []
        self._latest_by_account: dict[str, object] = {}
        self._refresh_in_flight = False
        self._pending = ui_models.PendingTransitions()

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
        self._render()

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

    def _launch(self, resource_key: str, method: ConnectMethod) -> None:
        """Dispatch a connect. Reachable only through an enabled method entry -- and no launcher
        change is applied in this build, so today every path here is fail-safe plumbing for
        add-web-launcher / add-native-launcher to complete (§4.2 spinner + FR-2-AC-5 fallback
        surface land with the launch calls themselves)."""
        account_id = self._auth.active_account_id
        if account_id is None:
            return
        self._method_prefs.record_use(account_id, resource_key, method)  # FR-2-AC-2
        self._present_launch_failure(
            resource_key,
            f"{_METHOD_LABELS[method]} is not available in this build yet",
            offer_web_fallback=method is ConnectMethod.NATIVE,
        )

    def _present_launch_failure(
        self, resource_key: str, reason: str, *, offer_web_fallback: bool
    ) -> None:
        """FR-2-AC-5's shared failure surface: the reason plus, for a native failure, a
        one-action web fallback on the same surface."""
        toast = Adw.Toast(title=reason)
        if offer_web_fallback:
            toast.set_button_label("Connect (Web)")
            toast.connect(
                "button-clicked",
                lambda *_: self._launch(resource_key, ConnectMethod.WEB),
            )
        self._toast_overlay.add_toast(toast)

    def _invoke_action(self, resource_key: str, action: str) -> None:
        """Dispatch a Cloud PC action. Reachable only through an enabled action entry, so today
        (actions service unapplied) this is plumbing for add-cloudpc-actions; the §7.4 pending
        suppression and FR-5-AC-1 toast behavior are already real."""
        if action == "reprovision":
            self._confirm_reprovision(resource_key)
            return
        self._dispatch_action(resource_key, action)

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
                self._dispatch_action(resource_key, "reprovision")
            # Anything else -- cancel, close, Esc -- dispatches nothing (FR-5-AC-2).

        dialog.connect("response", on_response)
        dialog.present(self)

    def _dispatch_action(self, resource_key: str, action: str) -> None:
        self._pending.mark(resource_key, action)  # §7.4 conflict suppression until refresh
        self._toast(f"{_ACTION_LABELS.get(action, action)} requested")
        self._render()
        # The actual Graph action call is add-cloudpc-actions work; on landing it schedules the
        # call here and drives the FR-5-AC-1 post-action refresh via refresh_after_action.

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

        groups = ui_models.build_groups(
            selection.entries, self._bookmarks, pending=self._pending
        )
        account_id = self._auth.active_account_id

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
        primary.connect("clicked", lambda *_: self._launch(row.key, preferred))

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
                lambda _b, m=method: (popover.popdown(), self._launch(row.key, m)),
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
